"""The confined launch spec offline: no credential in env, mounts, or args; the container canary;
the capability grant's shape; and the container-side agent files. Docker is not needed — these pin
the spec the Docker-marked adversarial suite then executes for real.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from evals.ab import arms
from evals.agent import container_boundary, escape_scan, secret_scan
from evals.confinement import launch

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
            launch.Upstream.provider("opencode-go", "https://api.opencode-go.example", str(provider_key)),
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
    assert provider["paths"] == ["*"] and "POST" in provider["methods"]
    dumped = json.dumps(config)
    assert KEY not in dumped and PROVIDER_KEY not in dumped


def test_the_entrypoint_starts_one_shim_per_upstream_and_holds_no_secret(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    text = launch.entrypoint_text(spec, ["pi", "--print", "do the task"])
    assert text.count("shim.py") == 2
    assert "--sock /broker/broker.sock" in text
    assert "--cap-file /run/capability.json" in text
    assert "exec pi --print 'do the task'" in text
    assert KEY not in text and PROVIDER_KEY not in text


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


def test_the_container_canary_flags_host_paths_not_container_state() -> None:
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

    assert escape_scan(transcript("sed -n 1,50p /Users/panda/.pi/agent/auth.json"), box) is not None
    assert escape_scan(transcript("cat /etc/passwd /Users/panda/.zshrc"), box) is not None
    assert "host path in a tool result" in (escape_scan(transcript("ls /task", "/Users/panda/secret"), box) or "")
    assert escape_scan(transcript("python3 -m unittest discover -s tests"), box) is None
    assert escape_scan(transcript("cat /task/src/app.py /scratch/agent/auth.json"), box) is None, (
        "the placeholder auth.json is not a credential; only host paths escape"
    )
    assert escape_scan(transcript("ls /usr/local/lib/node_modules"), box) is None


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
                "opencode-go": {"type": "api_key", "key": PROVIDER_KEY},
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
                        "baseUrl": "https://api.opencode-go.example",
                        "api": "openai-completions",
                        "apiKey": PROVIDER_KEY,
                        "models": [{"id": "deepseek-v4.1-flash"}],
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
    assert list(auth) == ["opencode-go"] and auth["opencode-go"]["key"] == arms.PLACEHOLDER_KEY
    models = json.loads((target / "models.json").read_text(encoding="utf-8"))
    entry = models["providers"]["opencode-go"]
    assert entry["baseUrl"] == "http://127.0.0.1:8080"
    assert entry["apiKey"] == arms.PLACEHOLDER_KEY
    assert entry["models"] == [{"id": "deepseek-v4.1-flash"}]
    carried = json.dumps(auth) + json.dumps(models)
    assert PROVIDER_KEY not in carried


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
