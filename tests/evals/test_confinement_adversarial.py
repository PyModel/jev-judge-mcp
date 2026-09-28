"""The confinement boundary's adversarial integration tests (marker `docker`, ADR-0074).

Everything here runs against real containers with a fake key and fake upstreams — no spend. The
adversary is a script INSIDE the agent container: it dumps the environment, walks `/proc`, greps
the filesystem for the key values, probes the known credential paths, tries direct egress, and
attacks the broker with disallowed destinations, methods, operations, and forged capabilities.
Every probe must fail or miss. The same container then drives the real wheel-installed MCP server
through the shim, the broker, and the fake TypeSafe upstream — the one allowlisted operation that
must succeed, with the real (fake) key injected and redacted on echo.

Skips cleanly without a Docker daemon, which is the Linux CI shape.
"""

import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest

from evals.ab import arms
from evals.confinement import launch
from evals.confinement.launch import (
    AGENT_GRANT_MOUNT,
    ConfinementSpec,
    Upstream,
    broker_argv,
    broker_config,
    entrypoint_text,
    mint_capability,
    wait_for_broker,
)

pytestmark = [pytest.mark.docker]

REPO = Path(__file__).resolve().parents[2]

KEY = "sk-adversarial-fake-typesafe-0123456789abcdefNOTREAL"
PROVIDER_KEY = "sk-adversarial-fake-provider-0123456789abcdefNOTREAL"


def DOCKER(test: Any) -> Any:
    """Skip inside the test, not at import (P3): a deselected module must not probe the daemon."""

    @functools.wraps(test)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        reason = launch.docker_available()
        if reason is not None:
            pytest.skip(f"needs a Docker daemon ({reason})")
        return test(*args, **kwargs)

    return wrapper


class _Upstream(BaseHTTPRequestHandler):
    """Answers like the real services, records every request, and always echoes the key so the
    broker's redaction is exercised on the live path."""

    seen: ClassVar[list[dict[str, Any]]] = []
    key = KEY

    def _answer(self, body: bytes) -> None:
        try:
            loaded: object = json.loads(body)
        except json.JSONDecodeError:
            loaded = None
        answers: dict[str, Any] = {}
        raw_questions: object = cast(dict[str, Any], loaded).get("questions") if isinstance(loaded, dict) else None
        if isinstance(raw_questions, dict):
            questions = cast(dict[str, Any], raw_questions)
            for qid, question in questions.items():
                # A choice question's options ride as the `criteria` keys (`domain.questions`).
                raw_criteria: object = (
                    cast(dict[str, Any], question).get("criteria") if isinstance(question, dict) else None
                )
                ids = [str(key) for key in cast(dict[str, Any], raw_criteria)] if isinstance(raw_criteria, dict) else []
                ids = ids or ["a", "b"]
                rest = 0.01 / (len(ids) - 1) if len(ids) > 1 else 0.0
                answers[qid] = {
                    "choice": ids[0],
                    "confidence": 0.99,
                    "probabilities": {i: (0.99 if i == ids[0] else rest) for i in ids},
                }
        payload = json.dumps(
            {"answers": answers, "usage": {"input_tokens": 3, "output_tokens": 3}, "echo": self.key}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Upstream-Echo", f"bearer {self.key}")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        type(self).seen.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": body.decode("utf-8", "replace")[:4000],
            }
        )
        self._answer(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


class _ProviderUpstream(_Upstream):
    seen: ClassVar[list[dict[str, Any]]] = []
    key = PROVIDER_KEY


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


@pytest.fixture(scope="module")
def image() -> str:
    """The python-only test image with this checkout's wheel installed — the same server the
    confined agent runs, without the agent CLIs (the workflow test's stub is a python script)."""
    if launch.docker_available() is not None:
        pytest.skip("needs a Docker daemon")
    wheel = arms.ensure_wheel()
    context = Path(__file__).parent / "adversarial-image"
    context.mkdir(exist_ok=True)
    (context / "Dockerfile").write_text(
        "FROM python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends git \\\n"
        "    && rm -rf /var/lib/apt/lists/*\n"
        "COPY jev_judge_mcp-*.whl /tmp/\n"
        "RUN wheel=$(ls /tmp/jev_judge_mcp-*.whl) \\\n"
        '    && pip install --no-cache-dir "$wheel[typesafe]" \\\n'
        "    && rm /tmp/jev_judge_mcp-*.whl\n",
        encoding="utf-8",
    )
    for stale in context.glob("jev_judge_mcp-*.whl"):
        stale.unlink()
    shutil.copyfile(wheel, context / wheel.name)
    tag = "jev-eval-adversarial:latest"
    build = subprocess.run(
        ["docker", "build", "-t", tag, str(context)],
        capture_output=True,
        text=True,
        timeout=900,
        env=launch.docker_env(),
    )
    assert build.returncode == 0, build.stderr[-2000:]
    return tag


ADVERSARY = """
import base64, json, os, socket, subprocess, sys
sys.path.insert(0, "/scratch/code")
import protocol

# Base64 on the wire so this script's own bytes are not the value the scan hunts for.
KEY1 = base64.b64decode(@KEY1@).decode()
KEY2 = base64.b64decode(@KEY2@).decode()
PORTA = @PORTA@
HOSTA = "host.internal"
report = {}

def check(name, ok, detail=""):
    report[name] = {"ok": bool(ok), "detail": str(detail)[:300]}

def frame(method, scheme, host, port, path, token, headers=None, body=b""):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(60)
        s.connect("/broker/broker.sock")
        protocol.send_frame(
            s,
            {
                "cap": token,
                "method": method,
                "scheme": scheme,
                "host": host,
                "port": port,
                "path": path,
                "headers": headers or [],
            },
            body,
        )
        return protocol.recv_frame(s)[0]

cap = json.load(open("/run/capability.json"))["token"]

# The agent's own environment and process metadata carry no credential.
dump = "\\n".join(f"{k}={v}" for k, v in os.environ.items())
check("env_free", KEY1 not in dump and KEY2 not in dump, dump[:200])
proc_hit = []
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        data = open(f"/proc/{pid}/environ", "rb").read()
    except OSError:
        continue
    if KEY1.encode() in data or KEY2.encode() in data:
        proc_hit.append(pid)
check("proc_environ_free", not proc_hit, proc_hit)

# The filesystem does not contain the key values.
hits = []
for root, dirs, files in os.walk("/"):
    dirs[:] = [d for d in dirs if not os.path.join(root, d).startswith(("/proc", "/sys", "/dev", "/broker"))]
    for name in files:
        p = os.path.join(root, name)
        try:
            if os.path.getsize(p) > 262144:
                continue
            data = open(p, "rb").read()
        except OSError:
            continue
        if KEY1.encode() in data or KEY2.encode() in data:
            hits.append(p)
check("filesystem_free", not hits, hits[:10])

# The known credential paths and the broker's own mounts are absent.
present = [p for p in (
    "/root/.pi/agent/auth.json", "/root/.claude.json", "/root/.claude",
    "/Users/panda/.pi/agent/auth.json", "/home/panda",
    "/secrets/typesafe.key", "/secrets/model-provider.key", "/grants", "/config.json",
    "/root/.aws/credentials", "/root/.ssh/id_rsa",
) if os.path.exists(p)]
check("credential_paths_absent", not present, present)
listing = os.listdir("/broker") if os.path.isdir("/broker") else None
check("socket_volume_holds_only_socket", listing == ["broker.sock"], listing)
mount_points = [line.split()[3] for line in open("/proc/self/mountinfo").read().splitlines() if len(line.split()) > 3]
crossed = [p for p in mount_points if p.startswith(("/secrets", "/grants")) or p == "/config.json"]
check("no_broker_mounts", not crossed, crossed)

# No egress: every direct connection is refused.
def refused(host, port):
    try:
        s = socket.create_connection((host, port), timeout=5)
        s.close()
        return False
    except OSError:
        return True
check("no_direct_typesafe", refused("api.typesafe.ai", 443))
check("no_direct_host", refused("host.internal", 1))

# The broker refuses everything not allowlisted, and the forged capability.
h = frame("POST", "http", "evil.example", 80, "/v1/systemone", cap)
check("refuses_destination", h.get("error") == "allowlist", h)
h = frame("GET", "http", HOSTA, PORTA, "/v1/systemone", cap)
check("refuses_method", h.get("error") == "allowlist", h)
h = frame("POST", "http", HOSTA, PORTA, "/v1/other", cap)
check("refuses_path", h.get("error") == "allowlist", h)
h = frame("POST", "http", HOSTA, PORTA, "/v1/systemone", "forged-capability-token-0123456789")
check("refuses_forged_capability", h.get("error") == "capability", h)

# One allowlisted jev operation succeeds through the real server and the real boundary.
env = dict(os.environ)
env.update({
    "JEV_PROVIDER": "typesafe",
    "JEV_MCP_MODEL": "adversarial-model",
    "TYPESAFE_BASE_URL": "http://127.0.0.1:8079",
    "TYPESAFE_API_KEY": @PLACEHOLDER@,
})
proc = subprocess.Popen(
    ["python3", "-m", "jev_judge_mcp"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
)
def send(msg):
    proc.stdin.write((json.dumps(msg) + "\\n").encode())
    proc.stdin.flush()
    return json.loads(proc.stdout.readline().decode())
init = send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2024-11-05", "capabilities": {},
    "clientInfo": {"name": "adversary", "version": "0"}}})
check("mcp_initialize", "result" in init, init)
proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\\n").encode())
proc.stdin.flush()
call = send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "jev_decide", "arguments": {
    "decision": "Which release branch ships the hotfix",
    "evidence": "The acceptance tests pass on branch ship and fail on branch hold.",
    "priorities": "Ship only passing builds; prefer speed.",
    "candidates": [
        {"id": "ship", "description": "Ship the passing branch"},
        {"id": "hold", "description": "Hold for re-review"},
    ],
}}})
content = (call.get("result") or {}).get("content") or [{}]
text = content[0].get("text", "") if "result" in call else json.dumps(call)[:300]
check(
    "mcp_jev_decide_ok",
    "result" in call and not call["result"].get("isError") and '"recommendation"' in text and '"selected"' in text,
    text[:300],
)
check("mcp_answer_carries_no_key", KEY1 not in text and KEY2 not in text, text[:200])
proc.stdin.close()
proc.terminate()
proc.wait(timeout=10)

print("ADVERSARY-REPORT " + json.dumps(report))
failed = [name for name, row in report.items() if not row["ok"]]
for name in failed:
    sys.stderr.write(f"FAILED PROBE {name}: {report[name]['detail']}\\n")
sys.exit(1 if failed else 0)
"""


@pytest.fixture()
def boundary_stack(image: str, tmp_path: Path) -> Any:
    """A live broker sidecar plus its fake upstreams, the capability, and the launch-layout mounts
    an agent container receives. `run_confined` builds its own broker; this fixture is the raw
    pieces for the adversary container, mounted exactly as `agent_argv` mounts them."""
    typesafe_server, porta = _serve(_Upstream)
    provider_server, portb = _serve(_ProviderUpstream)
    _Upstream.seen.clear()
    _ProviderUpstream.seen.clear()
    root = tmp_path / "stack"
    grants = root / "grants"
    code = root / "code"
    secrets = root / "secrets"
    for directory in (grants, code, secrets):
        directory.mkdir(parents=True)
    (secrets / "typesafe.key").write_text(KEY + "\n", encoding="utf-8")
    (secrets / "model-provider.key").write_text(PROVIDER_KEY + "\n", encoding="utf-8")
    source = REPO / "evals" / "confinement"
    for module in launch.MODULES:
        shutil.copyfile(source / module, code / module)
    upstreams = (
        Upstream(
            name="typesafe",
            scheme="http",
            host="host.internal",
            port=porta,
            methods=("POST",),
            paths=("/v1/systemone",),
            credential_file=str(secrets / "typesafe.key"),
            shim_port=8079,
        ),
        Upstream(
            name="model-provider",
            scheme="http",
            host="host.internal",
            port=portb,
            methods=("POST", "GET"),
            paths=("/chat/completions", "/models"),
            base_path="/zen/go/v1",
            credential_file=str(secrets / "model-provider.key"),
            shim_port=8080,
        ),
    )
    config_path = root / "broker-config.json"
    config_path.write_text(
        json.dumps(
            broker_config(
                upstreams,
                secret_mounts={"typesafe": "/secrets/typesafe.key", "model-provider": "/secrets/model-provider.key"},
                max_life_s=1800,
            )
        ),
        encoding="utf-8",
    )
    cap = mint_capability(grants, 900.0)
    tag = uuid.uuid4().hex[:12]
    volume = f"jev-eval-sock-{tag}"
    broker_name = f"jev-eval-broker-{tag}"
    spec = ConfinementSpec(agent_image=image, upstreams=upstreams, timeout_s=300.0)
    launch.docker_run(["docker", "volume", "create", volume])
    launch.docker_run(
        broker_argv(
            spec,
            name=broker_name,
            volume=volume,
            grants_dir=grants,
            config_path=config_path,
            code_dir=code,
            credential_files={"typesafe": secrets / "typesafe.key", "model-provider": secrets / "model-provider.key"},
        )
    )
    wait_for_broker(broker_name)
    try:
        yield {"spec": spec, "volume": volume, "broker": broker_name, "cap": cap, "code": code, "porta": porta}
    finally:
        launch.teardown(agent_name=f"jev-eval-agent-{tag}", broker_name=broker_name, volume=volume)
        typesafe_server.shutdown()
        provider_server.shutdown()
        shutil.rmtree(root, ignore_errors=True)


@DOCKER
def test_the_adversary_finds_no_credential_and_the_boundary_holds(boundary_stack: Any, tmp_path: Path) -> None:
    stack = boundary_stack
    spec = stack["spec"]
    scratch = tmp_path / "scratch-mount"
    (scratch / "code").mkdir(parents=True)
    for module in launch.MODULES:
        shutil.copyfile(stack["code"] / module, scratch / "code" / module)
    script = scratch / "adversary.py"
    import base64

    script.write_text(
        ADVERSARY.replace("@KEY1@", repr(base64.b64encode(KEY.encode()).decode()))
        .replace("@KEY2@", repr(base64.b64encode(PROVIDER_KEY.encode()).decode()))
        .replace("@PORTA@", str(stack["porta"]))
        .replace("@PLACEHOLDER@", repr(arms.PLACEHOLDER_KEY)),
        encoding="utf-8",
    )
    entrypoint = scratch / "entrypoint.sh"
    entrypoint.write_text(entrypoint_text(spec, ["python3", "/scratch/adversary.py"]), encoding="utf-8")
    argv = [
        "docker",
        "run",
        "--rm",
        "--name",
        "jev-eval-adversary",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "-v",
        f"{stack['volume']}:/broker:ro",
        "-v",
        f"{stack['cap'].grant_path}:{AGENT_GRANT_MOUNT}:ro",
        "-v",
        f"{scratch}:/scratch:ro",
        spec.agent_image,
        "/bin/sh",
        "/scratch/entrypoint.sh",
    ]
    result = launch.docker_run(argv, check=False, timeout=600)
    report_line = next((line for line in result.stdout.splitlines() if line.startswith("ADVERSARY-REPORT ")), "")
    assert result.returncode == 0, f"adversary failed:\n{result.stdout[-1000:]}\n{result.stderr[-3000:]}"
    report = json.loads(report_line.removeprefix("ADVERSARY-REPORT "))
    failed = {name: row for name, row in report.items() if not row["ok"]}
    assert not failed, f"failed probes: {failed}"
    assert len(report) >= 14, report.keys()
    # The upstream saw exactly the injected fake key, never the placeholder, never nothing.
    typesafe_calls = [row for row in _Upstream.seen if row["path"] == "/v1/systemone"]
    assert typesafe_calls, "the jev operation never reached the upstream"
    assert all(row["authorization"] == f"Bearer {KEY}" for row in typesafe_calls), _Upstream.seen
    assert not any(arms.PLACEHOLDER_KEY in (row["authorization"] or "") for row in typesafe_calls)


AGENT_IMAGE = "jev-eval-agent:latest"
"""The real runtime image (F2): pi, the adapter, claude, node, and the wheel. Built by
`make confinement-image`; the F2 test skips when it is absent so a stale image never lies."""


@DOCKER
def test_pi_starts_and_completes_inside_the_image(boundary_stack: Any, tmp_path: Path) -> None:
    """F2: the pi arm can actually start confined — the adapter loads from the image, the argv is
    accepted, and one prompt completes end to end through the shim, the broker, and a fake
    openai-completions provider whose base URL carries a path prefix."""
    if launch.docker_available() is not None:
        pytest.skip("needs a Docker daemon")
    probe = subprocess.run(
        ["docker", "image", "inspect", AGENT_IMAGE], capture_output=True, env=launch.docker_env(), check=False
    )
    if probe.returncode != 0:
        pytest.skip(f"{AGENT_IMAGE} is not built; run make confinement-image")
    stack = boundary_stack
    spec = stack["spec"]
    scratch = tmp_path / "scratch"
    for name in ("agent", "agent-cfg", "code", "home", "tmp"):
        (scratch / name).mkdir(parents=True)
    for module in launch.MODULES:
        shutil.copyfile(stack["code"] / module, scratch / "code" / module)
    from evals.confinement.launch import CONTAINER_ADAPTER

    provider = next(u for u in spec.upstreams if u.name != "typesafe")
    (scratch / "agent" / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    provider.name: {
                        "baseUrl": "http://127.0.0.1:8080",
                        "api": "openai-completions",
                        "apiKey": arms.PLACEHOLDER_KEY,
                        "models": [{"id": "stub-model", "contextWindow": 8192, "maxTokens": 1024}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (scratch / "agent" / "auth.json").write_text(
        json.dumps({provider.name: {"type": "api_key", "key": arms.PLACEHOLDER_KEY}}), encoding="utf-8"
    )
    (scratch / "agent-cfg" / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/scratch/home",
        "TMPDIR": "/scratch/tmp",
        "TERM": "dumb",
        "PI_CODING_AGENT_DIR": "/scratch/agent",
    }
    argv = [
        "pi",
        "--print",
        "--mode",
        "json",
        "--model",
        f"{provider.name}/stub-model",
        "--thinking",
        "high",
        "--no-extensions",
        "-e",
        CONTAINER_ADAPTER,
        "--mcp-config",
        "/scratch/agent-cfg/mcp.json",
        "--no-session",
        "--no-context-files",
        "--no-skills",
        "--no-prompt-templates",
        "--",
        "Reply with the single word ok",
    ]
    (scratch / "entrypoint.sh").write_text(
        entrypoint_text(
            spec,
            argv,
        ),
        encoding="utf-8",
    )
    _ProviderUpstream.seen.clear()
    try:
        result = launch.docker_run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                "jev-eval-f2",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                *(item for key, value in sorted(env.items()) for item in ("-e", f"{key}={value}")),
                "-v",
                f"{stack['volume']}:/broker:ro",
                "-v",
                f"{stack['cap'].grant_path}:{AGENT_GRANT_MOUNT}:ro",
                "-v",
                f"{scratch}:/scratch",
                AGENT_IMAGE,
                "/bin/sh",
                "/scratch/entrypoint.sh",
            ],
            check=False,
            timeout=300,
        )
    finally:
        pass
    assert result.returncode == 0, f"pi failed:\n{result.stdout[-500:]}\n{result.stderr[-1500:]}"
    assert "Unknown option" not in result.stderr and "Cannot find module" not in result.stderr
    assert '"type":"agent_end"' in result.stdout.replace(" ", ""), "the run must reach agent_end"
    calls = [row for row in _ProviderUpstream.seen if row["path"] == "/zen/go/v1/chat/completions"]
    assert calls, f"pi never called the provider through the broker: {_ProviderUpstream.seen}"
    assert calls[0]["authorization"] == f"Bearer {PROVIDER_KEY}", calls[0]


@DOCKER
def test_grading_runs_inside_the_image_and_trusts_nothing_host_side(image: str, tmp_path: Path) -> None:
    """F1: the post-run step never executes or follows agent-written trees on the host. A planted
    conftest.py tries to write a host marker, a symlink points at a host file, and neither the
    marker appears nor the target's content lands anywhere — while a real task still grades."""
    from evals.ab import tasks as ab_tasks
    from evals.confinement.launch import confined_postprocess

    task = ab_tasks.load_task("j10-control-spec")
    workdir = tmp_path / "tree"
    ab_tasks.materialize(task, workdir)
    marker = tmp_path / "host-marker.txt"
    (workdir / "tests" / "conftest.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran on host')\n", encoding="utf-8"
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("host-only-content-not-for-records", encoding="utf-8")
    (workdir / "planted-link").symlink_to(outside)
    patch, result = confined_postprocess(image, workdir, task.id, timeout_s=600)
    assert not marker.exists(), "the planted conftest must never run with host privileges"
    assert "host-only-content-not-for-records" not in patch, "a followed symlink leaked host bytes"
    assert result.acceptance_total > 0 and result.original_total > 0, "the task graded end to end"
    assert not result.regressions, "the untouched snapshot tree grades clean"


@DOCKER
def test_sigterm_tears_the_boundary_down_and_the_reaper_sweeps_leftovers(image: str, tmp_path: Path) -> None:
    """F5: SIGTERM unwinds teardown (containers, volume, temp root gone), and whatever a killed
    run still left behind is removed by the owner-label reaper on the next start."""

    child = tmp_path / "child.py"
    stub = tmp_path / "stub.py"
    stub.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
    key_file = tmp_path / "k.key"
    key_file.write_text("k-sigterm-fake-not-real\n", encoding="utf-8")
    child.write_text(
        "\n".join(
            [
                "import sys, time",
                "from pathlib import Path",
                f"sys.path.insert(0, {str(REPO)!r})",
                "from evals.agent import AgentCommand",
                "from evals.confinement import launch",
                "from evals.confinement.launch import ConfinementSpec, Upstream",
                "launch.exit_on_sigterm()",
                'up = Upstream(name="typesafe", scheme="http", host="host.invalid", port=1, '
                f'methods=("POST",), paths=("/v1/systemone",), credential_file={str(key_file)!r})',
                f"spec = ConfinementSpec(agent_image={image!r}, upstreams=(up,), timeout_s=600.0)",
                "from evals.bench import pi",
                "import shutil as _sh",
                "def _scratch_files(s):",
                f"    _sh.copyfile({str(stub)!r}, s / 'stub.py')",
                "print('READY', flush=True)",
                "try:",
                "    with launch.run_confined("
                "        AgentCommand(argv=lambda config: ['python3', '/scratch/stub.py'], timeout_s=500.0),"
                "        spec=spec, mcp_config={}, secret='k-sigterm-fake-not-real',"
                f"        run_dir=Path({str(tmp_path / 'records')!r}), parse=pi.parse, "
                "scratch_files=_scratch_files) as run:",
                "        print('STARTED', flush=True)",
                "        time.sleep(400)",
                "finally:",
                "    print('TOREDOWN', flush=True)",
            ]
        ),
        encoding="utf-8",
    )
    log = tmp_path / "child.log"
    with open(log, "w") as sink:
        proc = subprocess.Popen(
            [sys.executable, str(child)], stdout=sink, stderr=sink, env={**os.environ, "PYTHONPATH": str(REPO)}
        )
        # READY lands before the container starts; the SIGTERM then hits the run in flight —
        # the case that used to leak every container, volume, and key mount.
        deadline = time.time() + 120
        while "READY" not in log.read_text(encoding="utf-8", errors="replace") and time.time() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        assert "READY" in log.read_text(encoding="utf-8", errors="replace"), log.read_text(
            encoding="utf-8", errors="replace"
        )[-800:]
        proc.terminate()
        try:
            proc.wait(timeout=90)
        except subprocess.TimeoutExpired as error:
            proc.kill()
            raise AssertionError("SIGTERM did not unwind the child") from error
    assert "TOREDOWN" in log.read_text(encoding="utf-8", errors="replace")
    leftovers = launch.docker_run(["docker", "ps", "-aq", "--filter", "label=jev-eval"], check=False).stdout.strip()
    assert leftovers == "", f"teardown left containers: {leftovers}"
    # The reaper: forge the leftovers a killed run would leave, then sweep them.
    launch.docker_run(
        ["docker", "run", "-d", "--name", "jev-eval-broker-orphan", "--label", launch.OWNER_LABEL, "busybox", "true"],
        check=False,
    )
    launch.docker_run(
        ["docker", "volume", "create", "--label", launch.OWNER_LABEL, "jev-eval-sock-orphan"], check=False
    )
    stale = Path(tempfile.gettempdir()) / "jev-confined-orphan"
    stale.mkdir(exist_ok=True)
    os.utime(stale, (0, 0))
    removed = launch.reap()
    # `docker ps -aq --filter label=…` yields ids, so the container leg proves removal by absence.
    assert removed["containers"], removed
    still = launch.docker_run(
        ["docker", "ps", "-aq", "--filter", "name=jev-eval-broker-orphan"], check=False
    ).stdout.strip()
    assert still == "", "the orphan container survived the reaper"
    assert "jev-eval-sock-orphan" in removed["volumes"], removed
    assert "jev-confined-orphan" in removed["dirs"], removed
    shutil.rmtree(stale, ignore_errors=True)


@DOCKER
def test_the_without_jev_arm_has_no_typesafe_channel(boundary_stack: Any, tmp_path: Path) -> None:
    """F8 (2026-09-28 critique): arm A's boundary carries no TypeSafe upstream, so the in-container
    TypeSafe shim never starts and the broker holds no route: a direct call from inside arm A's
    container must fail, the model provider must still answer through the same boundary, and no
    TypeSafe request may reach the upstream."""
    from evals.ab.run import arm_spec
    from evals.agent import AgentCommand
    from evals.bench import pi

    spec = arm_spec(boundary_stack["spec"], "A")
    assert [upstream.name for upstream in spec.upstreams] == ["model-provider"]
    stub = "\n".join(
        [
            "import json, urllib.request",
            "refused = None",
            "try:",
            "    urllib.request.urlopen("
            '        urllib.request.Request("http://127.0.0.1:8079/v1/systemone", data=b"{}",'
            '        headers={"Content-Type": "application/json"}), timeout=30)',
            "except Exception as error:",
            "    refused = repr(error)",
            "assert refused, 'the TypeSafe channel answered inside the without-Jev arm'",
            'request = urllib.request.Request("http://127.0.0.1:8080/chat/completions", data=b"{}",',
            '    headers={"Content-Type": "application/json", "Authorization": "Bearer confined-placeholder"})',
            "with urllib.request.urlopen(request, timeout=60) as response:",
            "    assert response.status == 200, response.status",
            'print(json.dumps({"type": "message_end", "message": {"role": "assistant", "provider": "stub",',
            '    "model": "stub-model", "usage": {"input": 5, "output": 3, "cacheRead": 0, "cacheWrite": 0,'
            '    "totalTokens": 8, "cost": {"total": 0.01}}, "content": [{"type": "text", "text": refused}],'
            '    "stopReason": "stop"}}), flush=True)',
            'print(json.dumps({"type": "agent_end", "willRetry": False}), flush=True)',
        ]
    )
    run_dir = tmp_path / "records-a-arm"

    def scratch_files(scratch: Path) -> None:
        (scratch / "stub_agent.py").write_text(stub, encoding="utf-8")

    grant = "\n".join(
        [
            "try:",
            "    grant_text = open('/run/capability.json').read()",
            "except OSError:",
            "    grant_text = 'no grant file'",
            'print(json.dumps({"type": "message_end", "message": {"role": "assistant", "provider": "stub",',
            '    "model": "stub-model", "usage": {"input": 5, "output": 3, "cacheRead": 0, "cacheWrite": 0,'
            '    "totalTokens": 8, "cost": {"total": 0.01}}, "content": [{"type": "text", "text": grant_text}],'
            '    "stopReason": "stop"}}), flush=True)',
        ]
    )
    stub = stub.replace(
        'print(json.dumps({"type": "message_end"',
        grant + '\nprint(json.dumps({"type": "message_end"',
        1,
    )
    real_mint = launch.mint_capability

    def mint(grants_dir: Path, ttl_s: float) -> Any:
        """A known token, so the scrub assertion needs no peeking at the run's own secret."""
        import hashlib
        from dataclasses import replace as _replace

        cap = real_mint(grants_dir, ttl_s)
        known = "tok-f11-known-fake-not-a-real-credential"
        path = grants_dir / (hashlib.sha256(known.encode()).hexdigest() + ".json")
        path.write_text(json.dumps({"token": known, "expires": cap.expires}), encoding="utf-8")
        cap.grant_path.unlink()
        return _replace(cap, token=known, grant_path=path)

    original_mint = launch.mint_capability
    launch.mint_capability = mint
    try:
        with launch.run_confined(
            AgentCommand(argv=lambda config: ["python3", "/scratch/stub_agent.py"], timeout_s=120.0),
            spec=spec,
            mcp_config={},
            secret=KEY,
            run_dir=run_dir,
            prepare=_materialize,
            parse=pi.parse,
            scratch_files=scratch_files,
        ) as run:
            assert run.status == "ok", (run.status, run.stderr[-500:])
            # Reading its own grant is exploration at most, never a secret-scan void (F11).
            assert run.escape is None, run.escape
    finally:
        launch.mint_capability = original_mint
    assert not _Upstream.seen, f"arm A reached the TypeSafe upstream: {_Upstream.seen}"
    kept = "".join(p.read_text(encoding="utf-8", errors="replace") for p in run_dir.rglob("*") if p.is_file())
    assert "tok-f11-known-fake-not-a-real-credential" not in kept, "the token survived the scrub"


def _materialize(workdir: Path) -> None:
    (workdir / "task.txt").write_text("materialized", encoding="utf-8")


@DOCKER
def test_a_stub_agent_workflow_runs_confined_end_to_end(boundary_stack: Any, tmp_path: Path) -> None:
    """Acceptance 4: the run_agent-shaped workflow (records, transcript, workdir, teardown) works
    through `run_confined` with a stub agent — no Docker-only shortcuts, the real launcher."""
    from evals.agent import AgentCommand
    from evals.bench import pi

    spec = boundary_stack["spec"]
    stub = "\n".join(
        [
            "import json, urllib.request",
            "request = urllib.request.Request(",
            '    "http://127.0.0.1:8080/chat/completions",',
            '    data=b"{}",',
            '    headers={"Content-Type": "application/json", "Authorization": "Bearer confined-placeholder"},',
            ")",
            "with urllib.request.urlopen(request, timeout=60) as response:",
            "    assert response.status == 200, response.status",
            'print(json.dumps({"type": "message_end", "message": {"role": "assistant", "provider": "stub",',
            '    "model": "stub-model", "usage": {"input": 5, "output": 3, "cacheRead": 0, "cacheWrite": 0,',
            '    "totalTokens": 8, "cost": {"total": 0.01}}, "content": [{"type": "text", "text": "ok"}],'
            '    "stopReason": "stop"}}), flush=True)',
            'print(json.dumps({"type": "agent_end", "willRetry": False}), flush=True)',
            'open("/task/done.txt", "w").write("done")',
        ]
    )
    run_dir = tmp_path / "records"

    def scratch_files(scratch: Path) -> None:
        (scratch / "stub_agent.py").write_text(stub, encoding="utf-8")

    with launch.run_confined(
        AgentCommand(argv=lambda config: ["python3", "/scratch/stub_agent.py"], timeout_s=120.0),
        spec=spec,
        mcp_config={},
        secret=KEY,
        run_dir=run_dir,
        prepare=_materialize,
        parse=pi.parse,
        scratch_files=scratch_files,
    ) as run:
        assert (run.workdir / "task.txt").exists()
        assert run.status == "ok", (run.status, run.stderr[-500:])
        assert run.escape is None, run.escape
    assert (run_dir / "stream.jsonl").is_file()
    assert not run.workdir.exists(), "the workdir is gone with the context"
    kept = "".join(p.read_text(encoding="utf-8", errors="replace") for p in run_dir.rglob("*") if p.is_file())
    assert KEY not in kept
    leftover = launch.docker_run(
        ["docker", "ps", "-aq", "--filter", "label=jev-eval=agent"], check=False
    ).stdout.strip()
    assert leftover == "", f"agent containers survived teardown: {leftover}"
    provider_calls = _ProviderUpstream.seen
    assert provider_calls and all(row["authorization"] == f"Bearer {PROVIDER_KEY}" for row in provider_calls), (
        provider_calls
    )
    assert all(row["path"] == "/zen/go/v1/chat/completions" for row in provider_calls), (
        "F3: the shim's /chat/completions must arrive with the provider base path prepended"
    )
