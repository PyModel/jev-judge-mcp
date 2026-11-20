"""The confined launch spec offline: no credential in env, mounts, or args; the container canary;
the capability grant's shape; and the container-side agent files. Docker is not needed — these pin
the spec the Docker-marked adversarial suite then executes for real.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from evals.ab import arms, ledger, tasks
from evals.ab import run as ab_run
from evals.ab.grade import grade
from evals.agent import AgentCommand, container_boundary, escape_scan, exploration_paths, secret_scan
from evals.confinement import launch
from evals.isolation import IsolationResult
from evals.spend import SpendLedger

KEY = "sk-launch-fake-typesafe-key-0123456789abcdefNOTREAL"
PROVIDER_KEY = "sk-launch-fake-provider-key-0123456789abcdefNOTREAL"


def _spec(tmp_path: Path, **overrides: Any) -> launch.ConfinementSpec:
    key = tmp_path / "typesafe.key"
    key.write_text(KEY + "\n", encoding="utf-8")
    provider_key = tmp_path / "provider.key"
    provider_key.write_text(PROVIDER_KEY + "\n", encoding="utf-8")
    values: dict[str, Any] = {
        "agent_image": "jev-eval-agent:test",
        "upstreams": (
            launch.Upstream.typesafe(str(key)),
            launch.Upstream.provider("opencode-go", "https://opencode.example/zen/go/v1", str(provider_key)),
        ),
        "timeout_s": 900.0,
        "env": {"HOME": "/scratch/home", "TERM": "dumb"},
    }
    values.update(overrides)
    return launch.ConfinementSpec(**values)


def _mounts(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, flag in enumerate(argv) if flag == "-v"]


def test_the_agent_argv_has_no_network_only_the_runs_mounts_and_no_secret(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    argv = launch.agent_argv(
        spec,
        name="jev-eval-agent-x",
        volume="jev-eval-sock-x",
        workdir=tmp_path / "task",
        scratch=tmp_path / "scratch",
        grant_file=tmp_path / "grants" / "cap.json",
    )
    image = argv.index(spec.agent_image)
    head = argv[:image]
    assert "--network" in head and head[head.index("--network") + 1] == "none"
    assert "--cap-drop" in head and head[head.index("--cap-drop") + 1] == "ALL"
    mounts = _mounts(argv)
    assert mounts == [
        f"{tmp_path / 'task'}:/task",
        f"{tmp_path / 'scratch'}:/scratch",
        "jev-eval-sock-x:/broker:ro",
        f"{tmp_path / 'grants' / 'cap.json'}:/run/capability.json:ro",
    ], "the agent mounts only the workdir, its scratch, the socket volume, and its own grant"
    joined = "\\0".join(argv)
    assert KEY not in joined and PROVIDER_KEY not in joined
    env_pairs = [argv[i + 1] for i, flag in enumerate(argv) if flag == "-e"]
    assert env_pairs == [f"{key}={value}" for key, value in sorted(spec.env.items())]
    assert argv[image + 1 :] == ["/bin/sh", "/scratch/entrypoint.sh"]


def test_the_broker_alone_mounts_the_credentials_read_only(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    files = {name: tmp_path / f"{name}.key" for name in ("typesafe", "opencode-go")}
    argv = launch.broker_argv(
        spec,
        name="jev-eval-broker-x",
        volume="vol",
        grants_dir=tmp_path / "grants",
        config_path=tmp_path / "broker-config.json",
        code_dir=tmp_path / "code",
        credential_files=files,
    )
    mounts = _mounts(argv)
    assert f"{files['typesafe']}:/secrets/typesafe.key:ro" in mounts
    assert f"{files['opencode-go']}:/secrets/opencode-go.key:ro" in mounts
    assert all(mount.endswith(":ro") for mount in mounts if "/secrets/" in mount)
    assert "--read-only" in argv[: argv.index(spec.broker_image)]
    agent = launch.agent_argv(
        spec,
        name="jev-eval-agent-x",
        volume="vol",
        workdir=tmp_path / "task",
        scratch=tmp_path / "scratch",
        grant_file=tmp_path / "g.json",
    )
    agent_mounts = _mounts(agent)
    assert not any("/secrets/" in mount or "/grants" in mount or "broker-config" in mount for mount in agent_mounts)


def test_the_broker_config_names_only_container_paths_and_allowlisted_rules(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    config = launch.broker_config(
        spec.upstreams, secret_mounts={"typesafe": "/secrets/typesafe.key", "opencode-go": "/secrets/opencode-go.key"}
    )
    assert config["listen"] == "/sockets/broker.sock"
    assert config["grants_dir"] == "/grants"
    typesafe = config["upstreams"][0]
    assert (typesafe["host"], typesafe["methods"], typesafe["paths"]) == (
        "api.typesafe.ai",
        ["POST"],
        ["/v1/systemone"],
    )
    provider = config["upstreams"][1]
    assert provider["paths"] == ["/chat/completions", "/models"] and "POST" in provider["methods"]
    assert provider["base_path"] == "/zen/go/v1"
    assert config["max_lifetime_s"] > 0
    dumped = json.dumps(config)
    assert KEY not in dumped and PROVIDER_KEY not in dumped


def _wait_snippet(text: str) -> str:
    start = text.index("python3 -c '") + len("python3 -c '")
    end = text.index("' || exit 97", start)
    return text[start:end]


def test_the_entrypoint_starts_one_shim_per_upstream_and_holds_no_secret(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    text = launch.entrypoint_text(spec, ["pi", "--print", "do the task"])
    assert text.count("shim.py") == 2
    assert "--sock /broker/broker.sock" in text
    assert "--cap-file /run/capability.json" in text
    assert "exec pi --print 'do the task'" in text
    assert KEY not in text and PROVIDER_KEY not in text


@pytest.mark.parametrize("shim_ports", [(8079,), (8079, 8080), (8079, 8080, 8081)])
def test_the_shim_readiness_wait_is_valid_python_and_fatal(tmp_path: Path, shim_ports: tuple[int, ...]) -> None:
    """F4: the generated wait compiles for any port count, ends fatally (`|| exit 97`), and never
    emits the doubled-comma tuple literal that made the suite flaky."""
    spec = _spec(
        tmp_path,
        upstreams=tuple(
            launch.Upstream(f"u{i}", "https", f"h{i}", 443, ("POST",), ("/x",), "/tmp/k", shim_port=port)
            for i, port in enumerate(shim_ports)
        ),
    )
    text = launch.entrypoint_text(spec, ["true"])
    assert "|| exit 97" in text
    snippet = _wait_snippet(text)
    compile(snippet, "wait", "exec")
    assert f"for port in {shim_ports!r}:" in snippet
    assert ",," not in snippet


def test_the_capability_grant_is_sha256_named_ttl_bounded_and_revocable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    monkeypatch.setattr(launch, "CLOCK", lambda: 1_000.0)
    cap = launch.mint_capability(tmp_path, 900.0)
    grant = json.loads(cap.grant_path.read_text(encoding="utf-8"))
    assert grant["token"] == cap.token and grant["expires"] == 1_900.0
    assert cap.grant_path.name == hashlib.sha256(cap.token.encode()).hexdigest() + ".json"
    cap.grant_path.unlink()  # revocation is file removal; the broker test proves the refusal


def test_the_container_canary_stops_on_host_material_and_records_exploration() -> None:
    box = container_boundary({"HOME": "/scratch/home", "PI_CODING_AGENT_DIR": "/scratch/agent"})

    def transcript(command: str, result: str = "") -> str:
        return (
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]},
                }
            )
            + "\n"
            + (
                json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": result}]}})
                if result
                else ""
            )
        )

    # Host material in a tool RESULT is the hard stop: inside the container it can only leak.
    assert "host path in a tool result" in (escape_scan(transcript("ls /task", "/Users/operator/secret"), box) or "")
    assert "host path in a tool result" in (
        escape_scan(transcript("cat /etc/hosts", "/private/var/folders/xyz/secret"), box) or ""
    )
    # A host path or the placeholder in a COMMAND is out-of-task exploration, recorded, not a stop.
    host_try = transcript("sed -n 1,50p /Users/operator/.pi/agent/auth.json")
    assert escape_scan(host_try, box) is None
    assert exploration_paths(host_try, box) == ("/Users/operator/.pi/agent/auth.json",)
    placeholder = transcript("cat /task/src/app.py /scratch/agent/auth.json")
    assert escape_scan(placeholder, box) is None, (
        "the placeholder auth.json is not a credential; only host material in a result escapes"
    )
    assert exploration_paths(placeholder, box) == ("/scratch/agent/auth.json",)
    assert escape_scan(transcript("python3 -m unittest discover -s tests"), box) is None
    assert exploration_paths(transcript("python3 -m unittest discover -s tests"), box) == ()


def test_the_recorded_jev_payload_is_data_not_a_command() -> None:
    """The confined re-run's first attempt (j9-review-patch.B.r1) stopped as an escape on its
    only run, at $0.00 spent: the Jev review's stringified-JSON arguments embed the task's diff,
    and the diff's Python division (`total_cents / parts`) read as a bare filesystem-root target.
    A tool's data arguments are not a command, and the container owns its `/`, so neither the
    payload nor the same division inside a real command is an escape (ADR-0074)."""
    box = container_boundary({"HOME": "/scratch/home", "PI_CODING_AGENT_DIR": "/scratch/agent"})
    recorded = (Path(__file__).parent / "data" / "pi-mcp-jev-review-args.txt").read_text(encoding="utf-8")
    line = json.dumps(
        {
            "type": "tool_execution_start",
            "toolCallId": "call_00_9qdx8dixa5uki4sp9h3mp00d",
            "toolName": "mcp",
            "args": {"tool": "jev_jev_review", "args": recorded},
        }
    )
    assert "total_cents / parts" in recorded  # the trip this regression pins
    assert escape_scan(line, box) is None
    pi_bash = '{"type": "tool_execution_start", "toolCallId": "1", "toolName": "bash", "args": {"command": %s}}'
    assert escape_scan(pi_bash % json.dumps('cd /task && python3 -c "print(round(total_cents / parts))"'), box) is None
    assert escape_scan(pi_bash % json.dumps("find / -type d -name tests 2>/dev/null | head"), box) is None


def test_the_hard_stops_still_stop_whichever_tool_carries_them() -> None:
    """Host material in a tool result, and a reach for fixture material outside /task, void a
    confined study whatever tool carries them; the exploration relaxation never applies to
    these (the secret-scan hit is the third hard stop, merged into `escape` by the launcher)."""
    box = container_boundary({"HOME": "/scratch/home", "PI_CODING_AGENT_DIR": "/scratch/agent"})
    pi_bash = '{"type": "tool_execution_start", "toolCallId": "1", "toolName": "bash", "args": {"command": %s}}'
    recorded = (Path(__file__).parent / "data" / "pi-mcp-jev-review-args.txt").read_text(encoding="utf-8")
    mcp_result = (
        '{"type": "tool_execution_end", "toolCallId": "1", "toolName": "mcp", '
        '"result": {"content": [{"type": "text", "text": %s}]}}'
    )
    assert "host path in a tool result" in (
        escape_scan(mcp_result % json.dumps(f"reviewed: {recorded[:40]} /Users/operator/.pi/agent/auth.json"), box)
        or ""
    )
    # The fixture's real names, from any tool: the hidden acceptance test, task.json, and a
    # file-tool reach into the reference solution.
    assert "fixture reach" in (
        escape_scan(pi_bash % json.dumps("cat /fx/tasks/j6-docs-vs-code/acceptance_test.py"), box) or ""
    )
    assert "fixture reach" in (
        escape_scan(pi_bash % json.dumps("cat /evals/ab/fixture/tasks/j6-docs-vs-code/task.json"), box) or ""
    )
    assert "fixture reach" in (escape_scan(pi_bash % json.dumps("cat /x/task.json | head"), box) or "")
    read_tool = '{"type": "tool_execution_start", "toolCallId": "2", "toolName": "read", "args": {"path": %s}}'
    assert "fixture reach" in (escape_scan(read_tool % json.dumps("/fx/reference/fees.py"), box) or "")
    assert "fixture material in a tool result" in (
        escape_scan(mcp_result % json.dumps("gold: /var/distractors/doc-governs/refunds.py"), box) or ""
    )
    # No false positive: in-workdir names are exempt by construction, and a docs URL is not a path.
    assert escape_scan(pi_bash % json.dumps("mkdir -p /task/tests/acceptance/"), box) is None
    assert escape_scan(read_tool % json.dumps("tests/test_fees.py"), box) is None
    assert escape_scan(mcp_result % json.dumps("see https://docs.python.org/3/reference/datamodel.html"), box) is None


def test_out_of_task_exploration_records_the_evidence_paths() -> None:
    """The recorded j6-docs-vs-code.A.r2 shape: the agent hunts the answer key across its
    container — root greps, /run, /broker, /proc, the server's site-packages — and finds
    nothing. That is a per-run failure category with evidence paths, never a study stop."""
    box = container_boundary({"HOME": "/scratch/home", "PI_CODING_AGENT_DIR": "/scratch/agent"})
    commands = [
        "grep -rIl -e rate-only -e doc-governs / --exclude-dir=proc --exclude-dir=sys 2>/dev/null | head -50",
        "ls -la /run 2>/dev/null; cat /run/capability.json 2>/dev/null",
        "ls -laR /scratch/servers /scratch/agent-cfg 2>/dev/null | head -80 && cat /scratch/entrypoint.sh",
        "cat /proc/1/cmdline | tr '\\0' ' '; echo",
        "cat /usr/local/lib/python3.12/site-packages/jev_judge_mcp/instructions.py | head -40",
    ]
    stream = "\n".join(
        json.dumps(
            {
                "type": "tool_execution_start",
                "toolCallId": str(i),
                "toolName": "bash",
                "args": {"command": command},
            }
        )
        for i, command in enumerate(commands)
    )
    assert escape_scan(stream, box) is None
    evidence = exploration_paths(stream, box)
    assert "/" in evidence and "/run/capability.json" in evidence and "/scratch/entrypoint.sh" in evidence
    assert "/proc/1/cmdline" in evidence
    assert "/usr/local/lib/python3.12/site-packages/jev_judge_mcp/instructions.py" in evidence
    assert not any(path == "/task" or path.startswith("/task/") for path in evidence)
    # Work under /task itself, dev streams, and /usr/bin executables are not exploration.
    clean = [
        "cd /task && python3 -m unittest discover -s tests -v 2>&1 | tail -20",
        "/usr/bin/env python3 -m unittest",
        "true >/dev/null",
        # F1 regressions, from the r3 false explorations: inline programs and heredoc bodies are
        # data, and a redirection target is never glued onto a path.
        'cd /task && python3 -c "\nbase = total // parts\n"',
        "cd /task && python3 <<'EOF'\nx = a // b\nEOF",
        "ls /task/x 2>/dev/null",
    ]
    innocent = "\n".join(
        json.dumps(
            {
                "type": "tool_execution_start",
                "toolCallId": str(i),
                "toolName": "bash",
                "args": {"command": command},
            }
        )
        for i, command in enumerate(clean)
    )
    assert exploration_paths(innocent, box) == ()


def test_secret_scan_names_the_files_that_carry_the_value(tmp_path: Path) -> None:
    (tmp_path / "stream.jsonl").write_text("clean\n", encoding="utf-8")
    hit_dir = tmp_path / "inner"
    hit_dir.mkdir()
    (hit_dir / "result.json").write_text(json.dumps({"leak": KEY}), encoding="utf-8")
    assert secret_scan(tmp_path, KEY) == ("inner/result.json",)
    assert secret_scan(tmp_path, "not-present") == ()
    with pytest.raises(ValueError):
        secret_scan(tmp_path, "")


def test_the_scoped_provider_key_and_the_container_agent_files(tmp_path: Path) -> None:
    operator = tmp_path / "agent"
    operator.mkdir()
    (operator / "auth.json").write_text(
        json.dumps(
            {
                "opencode-go": {"type": "api_key", "key": PROVIDER_KEY, "oauth_refresh": "should-not-cross"},
                "other": {"type": "api_key", "key": "sk-other"},
            }
        ),
        encoding="utf-8",
    )
    (operator / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "opencode-go": {
                        "baseUrl": "https://opencode.example/zen/go/v1",
                        "api": "openai-completions",
                        "apiKey": PROVIDER_KEY,
                        "headers": {"X-Api-Key": "should-not-cross"},
                        "env": {"SECRET": "should-not-cross"},
                        "models": [{"id": "deepseek-v4.1-flash", "apiKey": "should-not-cross", "limit": 8}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert arms.scoped_provider_key(operator / "auth.json", "opencode-go") == PROVIDER_KEY
    assert arms.scoped_provider_key(operator / "auth.json", "missing") == ""
    target = tmp_path / "container-agent"
    arms.container_agent_files(
        target,
        provider="opencode-go",
        auth_entry={"type": "api_key", "key": PROVIDER_KEY},
        models_bytes=(operator / "models.json").read_bytes(),
        provider_shim_url="http://127.0.0.1:8080",
    )
    auth = json.loads((target / "auth.json").read_text(encoding="utf-8"))
    assert list(auth) == ["opencode-go"]
    assert auth["opencode-go"] == {"type": "api_key", "key": arms.PLACEHOLDER_KEY}, "F7: only type and key cross"
    models = json.loads((target / "models.json").read_text(encoding="utf-8"))
    entry = models["providers"]["opencode-go"]
    assert entry["baseUrl"] == "http://127.0.0.1:8080"
    assert entry["apiKey"] == arms.PLACEHOLDER_KEY
    assert entry["models"] == [{"id": "deepseek-v4.1-flash", "limit": 8}], "F7: credential-shaped keys drop"
    carried = json.dumps(auth) + json.dumps(models)
    assert PROVIDER_KEY not in carried
    assert "should-not-cross" not in carried and "oauth_refresh" not in carried


def test_the_confined_mcp_config_points_the_server_at_the_shim_with_the_placeholder(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    config = arms.confined_mcp_config(
        "B",
        scratch=scratch,
        server_env={"JEV_PROVIDER": "typesafe", "JEV_MCP_MODEL": "jev-1.13.0"},
        typesafe_shim_url="http://127.0.0.1:8079",
    )
    dumped = json.dumps(config)
    assert KEY not in dumped and PROVIDER_KEY not in dumped
    jev = config["mcpServers"]["jev"]
    assert jev["env"]["TYPESAFE_BASE_URL"] == "http://127.0.0.1:8079"
    assert jev["env"]["TYPESAFE_API_KEY"] == arms.PLACEHOLDER_KEY
    assert jev["args"][:3] == ["-m", "evals.ab.proxy", "/scratch/jev-calls.jsonl"]
    assert jev["args"][-2:] == ["-m", "jev_judge_mcp"]
    assert config["mcpServers"]["harness"]["args"] == ["/scratch/servers/harness_server.py"]
    assert (scratch / "servers" / "harness_server.py").is_file()
    arm_a = arms.confined_mcp_config("A", scratch=scratch, server_env={}, typesafe_shim_url="http://127.0.0.1:8079")
    assert "jev" not in arm_a["mcpServers"]


def test_the_provider_upstream_keeps_its_base_path_and_exact_endpoints() -> None:
    """F3: `Upstream.provider` keeps the base URL's path and swaps `*` for the API family's
    endpoints."""
    upstream = launch.Upstream.provider("opencode-go", "https://opencode.ai/zen/go/v1", "/tmp/key")
    assert (upstream.host, upstream.port, upstream.scheme) == ("opencode.ai", 443, "https")
    assert upstream.base_path == "/zen/go/v1"
    assert upstream.paths == ("/chat/completions", "/models")
    anthropic = launch.Upstream.provider("anthropic", "https://api.anthropic.com", "/tmp/key", api="anthropic")
    assert anthropic.credential_header == "x-api-key" and anthropic.credential_scheme == ""
    assert anthropic.paths == launch.ANTHROPIC_ENDPOINTS and anthropic.base_path == ""


def test_the_broker_mounts_the_operator_files_directly_with_no_copies(tmp_path: Path) -> None:
    """F10: `credential_files` hands back the operator's own paths; no copy exists anywhere, so
    nothing can linger at a loose mode after the run."""
    spec = _spec(tmp_path)
    files = launch.credential_files(spec)
    assert files == {"typesafe": tmp_path / "typesafe.key", "opencode-go": tmp_path / "provider.key"}
    assert all(path.stat().st_size > 0 for path in files.values())


def test_a_relay_copy_never_follows_a_planted_symlink(tmp_path: Path) -> None:
    """F1: a symlink the agent planted in its writable scratch never makes the host read another
    file; only a plain regular file crosses, by that name."""
    secret_file = tmp_path / "outside.txt"
    secret_file.write_text("host-only", encoding="utf-8")
    planted = tmp_path / "relay"
    planted.symlink_to(secret_file)
    target = tmp_path / "copied"
    assert launch.copy_regular_nofollow(planted, target) is False
    assert not target.exists()
    regular = tmp_path / "plain"
    regular.write_text("relay-bytes", encoding="utf-8")
    assert launch.copy_regular_nofollow(regular, target) is True
    assert target.read_text(encoding="utf-8") == "relay-bytes"
    directory = tmp_path / "dir"
    directory.mkdir()
    assert launch.copy_regular_nofollow(directory, tmp_path / "nope") is False


@pytest.mark.parametrize(
    ("alteration", "relay_tokens", "receipt_tokens", "expected_cost", "diagnostic"),
    [
        ("intact", 1_000_000, 1_000_000, 0.042, None),
        ("symlink", 1_000_000, 1_000_000, 0.042, "tampered"),
        ("hardlink", 1_000_000, 1_000_000, 0.042, "tampered"),
        ("truncated", None, 1_000_000, 0.042, "disagree"),
        ("mismatch", 1, 10_000_000, 0.42, "disagree"),
        ("oversized", 10_000_000, 10_000_000, 0.42, "unreadable"),
        ("missing-receipts", None, None, 0.25, "missing"),
        ("unfinished-receipts", None, 1_000_000, 0.25, "incomplete"),
        ("inconsistent-receipts", None, 10_000_000, 0.42, "inconsistent"),
        ("unreadable-receipts", None, 1_000_000, 0.25, "unreadable"),
        ("teardown-failure", 10_000_000, 10_000_000, 0.42, None),
    ],
)
def test_confined_ab_books_broker_usage_despite_relay_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    alteration: str,
    relay_tokens: int | None,
    receipt_tokens: int | None,
    expected_cost: float,
    diagnostic: str | None,
) -> None:
    archive_source: Path | None = None
    events: list[dict[str, object]] = []

    def docker(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal archive_source
        if "jev-eval=broker" in argv:
            archive_source = None
            events.clear()
        for mount in _mounts(argv):
            if mount.endswith(":/receipts.jsonl"):
                archive_source = Path(mount.removesuffix(":/receipts.jsonl"))
        if argv[:2] == ["docker", "stop"] and archive_source is not None and events:
            if alteration != "unfinished-receipts":
                events.append({"event": "seal", "started": 2 if alteration == "inconsistent-receipts" else 1})
            archive_source.write_text("".join(json.dumps(row) + "\n" for row in events))
            if alteration == "unreadable-receipts":
                archive_source.symlink_to(archive_source.rename(archive_source.with_suffix(".other")))
        if (
            alteration == "teardown-failure"
            and archive_source is not None
            and argv[:3] == ["docker", "rm", "-f"]
            and argv[-1].startswith("jev-eval-broker-")
        ):
            return subprocess.CompletedProcess(argv, 1, "", "unable to remove")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def capture(argv: list[str], **_kwargs: object) -> tuple[str, str, int]:
        assert all("/receipts.jsonl" not in mount for mount in _mounts(argv))
        scratch = Path(next(mount.removesuffix(":/scratch") for mount in _mounts(argv) if mount.endswith(":/scratch")))
        log = scratch / "jev-calls.jsonl"
        evidence = "" if relay_tokens is None else json.dumps({"input_tokens": relay_tokens}) + "\n"
        log.write_text(evidence)
        if alteration == "oversized":
            with log.open("r+b") as handle:
                handle.truncate(launch.STDOUT_CAP_BYTES + 1)
        if alteration in ("symlink", "hardlink"):
            other = log.rename(scratch / "other.jsonl")
            if alteration == "symlink":
                log.symlink_to(other)
            else:
                os.link(other, log)
        if archive_source is not None and receipt_tokens is not None:
            events.extend([{"event": "open"}, {"event": "start", "id": 1}])
            if alteration != "unfinished-receipts":
                events.append({"event": "settle", "id": 1, "input_tokens": receipt_tokens})
        return json.dumps({"type": "result", "total_cost_usd": agent_cost}), "", 0

    task = tasks.load_task("j1-refund-window")
    tree = tmp_path / "tree"
    tasks.materialize(task, tree)
    graded = grade(tree, task, sys.executable)
    monkeypatch.setattr(ab_run, "postprocess", Mock(return_value=IsolationResult("", graded)))
    monkeypatch.setattr(ab_run, "parser_for", Mock(return_value=ab_run.stream.parse))
    monkeypatch.setattr(launch, "docker_run", docker)
    monkeypatch.setattr(launch, "wait_for_broker", Mock())
    monkeypatch.setattr(launch, "capture_output", capture)
    if alteration == "teardown-failure":
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        request.addfinalizer(launch.reap)
    setup = ab_run.Setup("pi", (), {}, {}, KEY, sys.executable, confinement=_spec(tmp_path), task_list=(task,))
    out = tmp_path / "study"

    agent_cost = 1.7 if alteration == "teardown-failure" else 0
    result = ab_run.study(setup, out, repeats=1)
    if alteration == "teardown-failure":
        assert result.startswith("stopped: isolation infrastructure:")
    else:
        assert result == "all 1 pairs recorded"
    rid = ab_run.run_id(task.id, "B", 1)
    row = json.loads((out / rid / "result.json").read_text())
    with_book = SpendLedger.load(out / "ledger.json", ledger.POLICIES["pi"])
    try:
        assert with_book.runs[rid] == pytest.approx(expected_cost + agent_cost)
    finally:
        with_book.close()
    assert row["jev_cost_usd"] == pytest.approx(expected_cost)
    if diagnostic is None:
        assert row["jev_metering_issue"] is None
    else:
        assert diagnostic in row["jev_metering_issue"]
    if receipt_tokens is not None and alteration != "unreadable-receipts":
        assert (out / rid / "broker-receipts.jsonl").is_file()


def test_a_secret_in_the_container_env_is_refused_before_anything_starts(tmp_path: Path) -> None:
    spec = _spec(tmp_path, env={"HOME": "/scratch/home", "TYPESAFE_API_KEY": KEY})
    from evals.agent import AgentCommand
    from evals.confinement.launch import ConfinementError, run_confined

    with pytest.raises(ConfinementError, match="must not ride in the container env"):
        with run_confined(
            AgentCommand(argv=lambda config: ["true"], timeout_s=5.0),
            spec=spec,
            mcp_config={},
            secret=KEY,
            run_dir=tmp_path / "records",
        ):
            pass


@pytest.mark.parametrize("name", ["jev-calls.jsonl", "jev-calls.stderr"])
def test_scout_033_confined_archives_start_private(tmp_path: Path, name: str) -> None:
    import os
    import stat

    source = tmp_path / "source"
    source.write_bytes(b"synthetic evidence")
    target = tmp_path / name
    mask = os.umask(0o022)
    try:
        assert launch.copy_regular_nofollow(source, target)
    finally:
        os.umask(mask)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_bytes() == source.read_bytes()
    target.chmod(0o644)
    target.write_bytes(b"prior evidence")
    with pytest.raises(PermissionError):
        launch.copy_regular_nofollow(source, target)
    assert target.read_bytes() == b"prior evidence"


@pytest.mark.parametrize("failure", ["second_allocation", "layout", "teardown"])
def test_scout_011_confined_partial_setup_releases_every_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, failure: str
) -> None:
    import shutil
    import tempfile

    from evals.agent import AgentCommand

    owned = tmp_path / "scratch-parent"
    owned.mkdir()
    request.addfinalizer(lambda: shutil.rmtree(owned))
    monkeypatch.setattr(tempfile, "tempdir", str(owned))
    spec = _spec(tmp_path)
    run_dir = tmp_path / "records"
    run_dir.mkdir(mode=0o700)
    record = run_dir / "synthetic.txt"
    record.write_text(KEY)
    original_mkdtemp = tempfile.mkdtemp
    original_mkdir = Path.mkdir

    def allocate(suffix: str | None = None, prefix: str | None = None, dir: str | None = None) -> str:
        if failure == "second_allocation" and any(owned.iterdir()):
            raise OSError(failure)
        return original_mkdtemp(suffix, prefix, dir)

    def mkdir(path: Path, *args: Any, **kwargs: Any) -> None:
        if failure == "layout" and path.name == "agent-cfg":
            raise OSError(failure)
        original_mkdir(path, *args, **kwargs)

    def docker(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("synthetic volume creation failure")

    def teardown(**_kwargs: Any) -> None:
        if failure == "teardown":
            raise OSError("teardown")

    monkeypatch.setattr(tempfile, "mkdtemp", allocate)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(launch, "docker_run", docker)
    monkeypatch.setattr(launch, "teardown", teardown)
    message = "synthetic volume creation failure" if failure == "teardown" else failure
    with pytest.raises(OSError, match=message) as primary:
        with launch.run_confined(
            AgentCommand(argv=lambda _: ["true"], timeout_s=1), spec=spec, mcp_config={}, secret=KEY, run_dir=run_dir
        ):
            pytest.fail("setup was expected to stop")
    if failure == "teardown":
        assert primary.value.__notes__ == ["Confinement cleanup also failed: OSError"]
        # Uncertain Docker cleanup keeps only its owner identity for the next reaper.
        [retry] = owned.iterdir()
        assert [p.name for p in retry.iterdir()] == [launch.LEASE_MARKER]
        import subprocess

        monkeypatch.setattr(launch, "docker_run", Mock(return_value=subprocess.CompletedProcess([], 0, "", "")))
        assert launch.reap()["dirs"] == [retry.name]
    assert list(owned.iterdir()) == []
    assert KEY not in record.read_text()


@pytest.mark.parametrize("budget", [8, 9])
def test_relay_budget_is_shared_and_overflow_unwinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int
) -> None:
    import subprocess

    from evals import agent

    records = tmp_path / "records"
    cleanup = Mock()
    docker = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    capture = Mock(return_value=("", "", 0))
    monkeypatch.setattr(agent, "RELAY_CAP_BYTES", budget, raising=False)
    monkeypatch.setattr(launch, "wait_for_broker", Mock())
    monkeypatch.setattr(launch, "docker_run", docker)
    monkeypatch.setattr(launch, "capture_output", capture)
    monkeypatch.setattr(launch, "teardown", cleanup)

    def relays(scratch: Path) -> None:
        (scratch / "jev-calls.jsonl").write_bytes(b"12345")
        (scratch / "jev-calls.stderr").write_bytes(b"6789")

    with launch.run_confined(
        agent.AgentCommand(argv=lambda _: ["true"], timeout_s=1),
        spec=_spec(tmp_path),
        mcp_config={},
        secret=KEY,
        run_dir=records,
        scratch_files=relays,
    ) as run:
        if budget == 8:
            assert run.relay_issue == "relay log tampered or unreadable (jev-calls.stderr)"
            assert not (records / "jev-calls.stderr").exists()
        else:
            assert run.relay_issue is None
            assert (records / "jev-calls.stderr").read_bytes() == b"6789"
    cleanup.assert_called_once()
    volume = cleanup.call_args.kwargs["volume"]
    label = f"{launch.LEASE_LABEL}={volume.removeprefix('jev-eval-sock-')}"
    volume_call, broker_call, agent_removal, broker_stop = docker.call_args_list
    assert all(label in call.args[0] for call in (volume_call, broker_call))
    assert agent_removal.args[0] == ["docker", "rm", "-f", cleanup.call_args.kwargs["agent_name"]]
    assert broker_stop.args[0] == ["docker", "stop", "-t", "2", cleanup.call_args.kwargs["broker_name"]]
    assert label in capture.call_args.args[0]
    assert sum(p.stat().st_size for p in records.glob("jev-calls.*")) <= budget


@pytest.mark.parametrize("returncode", [0, None], ids=["exit", "timeout"])
def test_the_agent_is_removed_before_its_final_relay_log_is_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int | None
) -> None:
    records = tmp_path / "records"
    active: str | None = None
    relay: Path | None = None

    def capture(argv: list[str], **_kwargs: object) -> tuple[str, str, int | None]:
        nonlocal active, relay
        active = argv[argv.index("--name") + 1]
        scratch = next(mount.removesuffix(":/scratch") for mount in _mounts(argv) if mount.endswith(":/scratch"))
        relay = Path(scratch) / "jev-calls.jsonl"
        return "", "", returncode

    def docker(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal active
        if active is not None and argv == ["docker", "rm", "-f", active]:
            assert relay is not None
            relay.write_text('{"input_tokens":1}\n')
            active = None
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(launch, "capture_output", capture)
    monkeypatch.setattr(launch, "docker_run", docker)
    monkeypatch.setattr(launch, "wait_for_broker", Mock())
    with launch.run_confined(
        AgentCommand(argv=lambda _: ["true"], timeout_s=1),
        spec=_spec(tmp_path),
        mcp_config={},
        secret=KEY,
        run_dir=records,
    ):
        assert active is None, "grading can start while the agent container is still running"
        assert (records / "jev-calls.jsonl").read_text() == '{"input_tokens":1}\n'


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_shutdown_signal_unwinds_cleanup(tmp_path: Path, signum: signal.Signals) -> None:
    cleanup = tmp_path / "cleanup"
    script = """
import os, signal, sys
from pathlib import Path
from evals.confinement.launch import exit_on_sigterm

exit_on_sigterm()
try:
    os.kill(os.getpid(), int(sys.argv[1]))
finally:
    Path(sys.argv[2]).touch()
"""
    result = subprocess.run([sys.executable, "-c", script, str(signum), str(cleanup)], check=False, timeout=10)
    assert cleanup.is_file()
    assert result.returncode == 128 + signum


def test_secret_scan_and_scrub_read_bounded_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evals import agent

    path = tmp_path / "artifact"
    original = b"1234567secretsecret.xsecret.las"
    path.write_bytes(original)
    monkeypatch.setattr(agent, "ARTIFACT_CHUNK_BYTES", 8, raising=False)
    fdopen = agent.os.fdopen

    class BoundedReader:
        def __init__(self, stream: Any) -> None:
            self.stream = stream

        def __enter__(self) -> Any:
            self.stream.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self.stream.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.stream, name)

        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 8, "artifact read exceeds the scan budget"
            return self.stream.read(size)

    def bounded_fdopen(fd: int, mode: str) -> BoundedReader:
        return BoundedReader(fdopen(fd, mode))

    monkeypatch.setattr(agent.os, "fdopen", bounded_fdopen)
    assert agent.secret_scan(tmp_path, "secret") == ("artifact",)
    agent.secret_scrub(tmp_path, "secret")
    assert path.read_bytes() == original.replace(b"secret", agent.REDACTED)
    assert agent.secret_scan(tmp_path, "secret") == ()


@pytest.mark.parametrize("slow", ["exec", "inspect", "logs", "sleep"])
def test_broker_deadline_bounds_each_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slow: str) -> None:
    import subprocess

    now = 0.0
    calls: list[str] = []

    def docker(argv: list[str], *, check: bool, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
        nonlocal now
        calls.append(argv[1])
        assert 0 < timeout <= 1 - now + 1e-9
        if argv[1] == slow:
            now += timeout
            raise subprocess.TimeoutExpired(argv, timeout)
        now += min(0.4, timeout)
        return subprocess.CompletedProcess(argv, int(argv[1] == "exec"), "true" if slow == "sleep" else "false", "")

    def sleep(delay: float) -> None:
        nonlocal now
        assert delay <= 1 - now + 1e-9
        now += delay

    monkeypatch.setattr(launch.time, "monotonic", lambda: now)
    monkeypatch.setattr(launch.time, "sleep", sleep)
    monkeypatch.setattr(launch, "docker_run", docker)
    with pytest.raises(launch.ConfinementError, match="within"):
        launch.wait_for_broker("fixture", 1.0)
    assert now == pytest.approx(1)
    assert (
        calls
        == {
            "exec": ["exec"],
            "inspect": ["exec", "inspect"],
            "logs": ["exec", "inspect", "logs"],
            "sleep": ["exec", "inspect"],
        }[slow]
    )


@pytest.mark.parametrize("failed_removal", [False, True])
def test_reaper_respects_live_leases_and_reports_only_deletions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_removal: bool
) -> None:
    import fcntl
    import os
    import subprocess
    from contextlib import ExitStack

    removed: list[str] = []
    roots = [tmp_path / ("jev-confined-v1-" + tag) for tag in ("active1", "active2", "orphan")]
    unknown = tmp_path / "jev-confined-legacy"
    unknown.mkdir(mode=0o700)
    os.utime(unknown, (0, 0))
    for directory in roots:
        directory.mkdir(mode=0o700)
        (directory / ".jev-lease-v1").touch(mode=0o600)
        os.utime(directory, (0, 0))
    monkeypatch.setattr(launch.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(launch.shutil, "which", Mock(return_value="fixture-docker"))

    def docker(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if "--filter" in argv:
            selection = argv[argv.index("--filter") + 1]
            kind = "container" if argv[1] == "ps" else "volume"
            names = [
                kind + "-" + r.name for r in roots if selection in ("label=jev-eval", "label=jev-eval-lease=" + r.name)
            ]
            return subprocess.CompletedProcess(argv, 0, "\n".join(names), "")
        if failed_removal and argv[-1].startswith("volume"):
            return subprocess.CompletedProcess(argv, 1, "", "fixture failure")
        removed.append(argv[-1])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(launch, "docker_run", docker)
    with ExitStack() as locks:
        for directory in roots[:2]:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            locks.callback(os.close, fd)
            fcntl.flock(fd, fcntl.LOCK_EX)
        if failed_removal:
            with pytest.raises(launch.ConfinementError, match="reap"):
                launch.reap()
        else:
            result = launch.reap()
            assert result["dirs"] == [roots[-1].name]
            assert result["volumes"] == ["volume-" + roots[-1].name]
            assert result["containers"] == ["container-" + roots[-1].name]
    assert unknown.is_dir() and all(r.is_dir() for r in roots[:2])
    assert roots[-1].exists() == failed_removal
    assert removed == ["container-" + roots[-1].name] + ([] if failed_removal else ["volume-" + roots[-1].name])


def test_reaper_keeps_identity_after_directory_removal_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch.tempfile, "tempdir", str(tmp_path))
    root = tmp_path / (launch.KEY_PREFIX + "fixture")
    root.mkdir(mode=0o700)
    with launch.directory_lease(root):
        assert launch.reap()["dirs"] == []
    payload = root / "payload"
    payload.mkdir()
    with monkeypatch.context() as patch:
        patch.setattr(launch.shutil, "rmtree", Mock(side_effect=PermissionError("fixture cleanup failure")))
        with pytest.raises(PermissionError, match="fixture cleanup failure"):
            launch.reap()
    assert (root / launch.LEASE_MARKER).is_file()
    assert launch.reap()["dirs"] == [root.name]
    assert not root.exists()


def test_missing_docker_volume_is_already_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    argv = ["docker", "volume", "rm", "fixture"]
    result = subprocess.CompletedProcess(argv, 1, "", "Error response from daemon: get fixture: no such volume")
    success = subprocess.CompletedProcess(["docker", "rm"], 0, "", "")
    monkeypatch.setattr(launch, "docker_run", Mock(side_effect=[success, success, result]))
    launch.teardown(agent_name="agent", broker_name="broker", volume="fixture")


def test_grading_interrupt_preserves_reaper_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(launch, "docker_run", Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        launch.confined_reference("fixture", "fixture", timeout_s=1)
    roots = list(tmp_path.glob(launch.RUN_PREFIX + "*"))
    assert len(roots) == 1 and list(roots[0].iterdir()) == [roots[0] / launch.LEASE_MARKER]
    monkeypatch.setattr(launch, "docker_run", Mock(return_value=Mock(returncode=0, stdout="")))
    assert launch.reap()["dirs"] == [roots[0].name]
