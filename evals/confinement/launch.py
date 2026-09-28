"""Host-side confinement for live agent eval runs (ADR-0074): the broker sidecar and the sandboxed
agent container, started and torn down by the harness.

The chain: the operator's key file and a scoped model-provider key are bind-mounted read-only into
the broker container alone; the broker listens on a Unix socket in a per-run volume; the agent
container runs with `--network none` and mounts only the task workdir (rw), its scratch (rw), the
socket volume (ro), and its own capability grant file (ro). A macOS-host Unix socket cannot be
bind-mounted into an OrbStack container — the listener lives in the macOS kernel and the connect
happens in the Linux VM kernel, so the mount surfaces the socket file but `connect(2)` returns
ECONNREFUSED — so the broker runs as a sidecar container and a shared docker volume carries the
socket (verified 2026-09-28; the alternatives and the decision are ADR-0074).

Every docker command is built from paths, never values: the launch spec carries no credential in
its environment, mounts, or arguments (`tests/evals/test_confinement_launch.py` pins that), the
agent's own config carries only the placeholder, and the capability token is bounded by the run
timeout and revoked at run end by removing the grant file.
"""

import hashlib
import json
import os
import secrets as _secrets
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from evals.ab import stream
from evals.agent import (
    STDOUT_CAP_BYTES,
    AgentCommand,
    AgentRunResult,
    capture_output,
    container_boundary,
    escape_scan,
    run_status,
    secret_scan,
    secret_scrub,
    write_run_logs,
)

BROKER_IMAGE = "python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
"""`python:3.12-slim` by digest. The broker and shim are stdlib-only and mounted in at run time,
so the sidecar needs nothing but a Python interpreter."""

CAPABILITY_MARGIN_S = 120.0
"""Grant TTL past the run timeout: a run may be booked and torn down late, never refused early."""

BROKER_READY_TIMEOUT_S = 30.0

CONTAINER_CODE = "/code"
CONTAINER_GRANTS = "/grants"
CONTAINER_SECRETS = "/secrets"
CONTAINER_SOCKETS = "/sockets"
CONTAINER_SOCKET = f"{CONTAINER_SOCKETS}/broker.sock"
AGENT_SOCKET_MOUNT = "/broker/broker.sock"
AGENT_GRANT_MOUNT = "/run/capability.json"
AGENT_TASK = "/task"
AGENT_SCRATCH = "/scratch"
AGENT_CONFIG = f"{AGENT_SCRATCH}/agent-cfg/mcp.json"
"""Fixed container paths. The agent-visible config and argv name only these."""

MODULES = ("protocol.py", "broker.py", "shim.py")
"""The stdlib-only confinement modules copied into both containers at run time."""

CLOCK = time.time
"""Injectable now, so grant expiry tests need no sleeping."""


class ConfinementError(RuntimeError):
    """The confinement boundary could not be built or verified. Not a run: nothing is booked."""


@dataclass(frozen=True)
class Upstream:
    """One allowlisted egress destination, and the credential the broker injects for it."""

    name: str
    scheme: str
    host: str
    port: int
    methods: tuple[str, ...]
    paths: tuple[str, ...]
    credential_file: str
    """Host path, bind-mounted read-only into the broker container only."""
    credential_header: str = "Authorization"
    credential_scheme: str = "Bearer "
    shim_port: int = 0
    """The in-container loopback port this upstream's shim serves; 0 means no shim (no client)."""

    @classmethod
    def typesafe(cls, credential_file: str, shim_port: int = 8079) -> "Upstream":
        """The TypeSafe API exactly as the jev tools call it: one operation, one method."""
        return cls(
            name="typesafe",
            scheme="https",
            host="api.typesafe.ai",
            port=443,
            methods=("POST",),
            paths=("/v1/systemone",),
            credential_file=credential_file,
            shim_port=shim_port,
        )

    @classmethod
    def provider(
        cls, name: str, base_url: str, credential_file: str, api: str = "", shim_port: int = 8080
    ) -> "Upstream":
        """The agent's model provider: the host is pinned, the paths are its own (`"*"`)."""
        parts = urlsplit(base_url if "://" in base_url else f"https://{base_url}")
        if not parts.hostname:
            raise ConfinementError(f"provider base URL has no host: {base_url}")
        anthropic = "anthropic" in api.lower()
        return cls(
            name=name,
            scheme=parts.scheme or "https",
            host=parts.hostname,
            port=parts.port or (80 if parts.scheme == "http" else 443),
            methods=("POST", "GET"),
            paths=("*",),
            credential_file=credential_file,
            credential_header="x-api-key" if anthropic else "Authorization",
            credential_scheme="" if anthropic else "Bearer ",
            shim_port=shim_port,
        )

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


@dataclass(frozen=True)
class Capability:
    token: str
    expires: float
    grant_path: Path


def mint_capability(grants_dir: Path, ttl_s: float) -> Capability:
    """A per-run capability: a random token in a grant file named by the token's sha256.

    The token is not the key. Its TTL is the run timeout plus a margin, and removing the grant
    file revokes it: the broker re-reads grants per request, so a revoked token is refused on the
    next call even if the agent kept a copy of it.
    """
    grants_dir.mkdir(parents=True, exist_ok=True)
    token = _secrets.token_urlsafe(24)
    expires = CLOCK() + ttl_s
    grant_path = grants_dir / f"{hashlib.sha256(token.encode()).hexdigest()}.json"
    grant_path.write_text(json.dumps({"token": token, "expires": expires}) + "\n", encoding="utf-8")
    grant_path.chmod(0o644)
    return Capability(token=token, expires=expires, grant_path=grant_path)


@dataclass(frozen=True)
class ConfinementSpec:
    """Everything a confined run needs; carries paths only, never a credential value."""

    agent_image: str
    upstreams: tuple[Upstream, ...]
    timeout_s: float
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    """The agent container's environment: placeholders and runtime settings, no secret values."""
    adapter_source: str = ""
    """Host path of the pi MCP adapter, copied into the scratch and used at /scratch/adapter."""
    uid: int = 0
    gid: int = 0
    broker_image: str = BROKER_IMAGE

    def shim_url(self, name: str) -> str:
        for upstream in self.upstreams:
            if upstream.name == name and upstream.shim_port:
                return f"http://127.0.0.1:{upstream.shim_port}"
        return ""


def docker_available() -> str | None:
    """None when the docker daemon answers; a short reason when it does not."""
    try:
        probe = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=30,
            env=docker_env(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"docker is unusable: {type(error).__name__}"
    if probe.returncode != 0:
        return "docker info failed: is OrbStack running?"
    return None


_DOCKER_ENV_KEYS = ("PATH", "HOME", "USER", "LOGNAME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "TERM")


def docker_env() -> dict[str, str]:
    """The docker CLI's own environment, filtered to what reaching the daemon needs."""
    return {key: os.environ[key] for key in _DOCKER_ENV_KEYS if key in os.environ}


def docker_run(argv: Sequence[str], *, check: bool = True, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(list(argv), capture_output=True, text=True, check=False, timeout=timeout, env=docker_env())
    if check and result.returncode != 0:
        raise ConfinementError(f"{' '.join(argv[:3])} failed: {result.stderr.strip()[:300]}")
    return result


def image_present(image: str) -> bool:
    probe = docker_run(["docker", "image", "inspect", image], check=False, timeout=60)
    return probe.returncode == 0


def broker_config(upstreams: Sequence[Upstream], *, secret_mounts: Mapping[str, str]) -> dict[str, Any]:
    """The broker container's config document. `secret_mounts` maps upstream name → container path."""
    return {
        "listen": CONTAINER_SOCKET,
        "grants_dir": CONTAINER_GRANTS,
        "upstreams": [
            {
                "name": upstream.name,
                "scheme": upstream.scheme,
                "host": upstream.host,
                "port": upstream.port,
                "methods": list(upstream.methods),
                "paths": list(upstream.paths),
                "credential_file": secret_mounts[upstream.name],
                "credential_header": upstream.credential_header,
                "credential_scheme": upstream.credential_scheme,
            }
            for upstream in upstreams
        ],
    }


def broker_argv(
    spec: ConfinementSpec,
    *,
    name: str,
    volume: str,
    grants_dir: Path,
    config_path: Path,
    code_dir: Path,
    credential_files: Mapping[str, Path],
) -> list[str]:
    """`docker run -d` for the broker sidecar. Credential files mount read-only, here only."""
    argv = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--label",
        "jev-eval=broker",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "-v",
        f"{volume}:{CONTAINER_SOCKETS}",
        "-v",
        f"{grants_dir}:{CONTAINER_GRANTS}:ro",
        "-v",
        f"{config_path}:/config.json:ro",
        "-v",
        f"{code_dir}:{CONTAINER_CODE}:ro",
    ]
    for upstream in spec.upstreams:
        argv += ["-v", f"{credential_files[upstream.name]}:{CONTAINER_SECRETS}/{upstream.name}.key:ro"]
    argv += [spec.broker_image, "python3", f"{CONTAINER_CODE}/broker.py", "/config.json"]
    return argv


def agent_argv(
    spec: ConfinementSpec,
    *,
    name: str,
    volume: str,
    workdir: Path,
    scratch: Path,
    grant_file: Path,
) -> list[str]:
    """`docker run --rm` (attached) for the agent: no network, and only the run's own mounts."""
    argv = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--label",
        "jev-eval=agent",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "-u",
        f"{spec.uid}:{spec.gid}",
        "-w",
        AGENT_TASK,
        "-v",
        f"{workdir}:{AGENT_TASK}",
        "-v",
        f"{scratch}:{AGENT_SCRATCH}",
        "-v",
        f"{volume}:/broker:ro",
        "-v",
        f"{grant_file}:{AGENT_GRANT_MOUNT}:ro",
    ]
    for key, value in sorted(spec.env.items()):
        argv += ["-e", f"{key}={value}"]
    argv += [spec.agent_image, "/bin/sh", f"{AGENT_SCRATCH}/entrypoint.sh"]
    return argv


def entrypoint_text(spec: ConfinementSpec, agent_argv_inside: Sequence[str]) -> str:
    """The agent container's launcher: one shim per shimmed upstream, a wait until each is
    accepting, then the agent. The wait closes the bind race: `&` alone would let a fast agent
    reach the loopback port before the shim's first listen."""
    lines = ["#!/bin/sh"]
    ports = [upstream.shim_port for upstream in spec.upstreams if upstream.shim_port]
    for upstream in spec.upstreams:
        if upstream.shim_port:
            lines += [
                f"python3 {AGENT_SCRATCH}/code/shim.py"
                f" --listen 127.0.0.1:{upstream.shim_port}"
                f" --upstream {upstream.url}"
                f" --sock {AGENT_SOCKET_MOUNT}"
                f" --cap-file {AGENT_GRANT_MOUNT} &"
            ]
    if ports:
        ports_literal = ", ".join(f"{port}," for port in ports)
        lines += [
            "python3 -c 'import socket, time, sys",
            f"for port in ({ports_literal}):",
            "    for _ in range(200):",
            "        try:",
            '            s = socket.create_connection(("127.0.0.1", port), 0.5); s.close(); break',
            "        except OSError:",
            "            time.sleep(0.05)",
            "    else:",
            '        sys.exit(f"shim on {port} never listened")\'',
        ]
    lines.append(f"exec {shlex.join(list(agent_argv_inside))}")
    return "\n".join(lines) + "\n"


def wait_for_broker(name: str, timeout_s: float = BROKER_READY_TIMEOUT_S) -> None:
    """Block until the sidecar's socket exists; a container that dies startup fails loudly."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe = docker_run(
            [
                "docker",
                "exec",
                name,
                "python3",
                "-c",
                f"import sys; sys.exit(0 if __import__('os').path.exists({CONTAINER_SOCKET!r}) else 1)",
            ],
            check=False,
            timeout=30,
        )
        if probe.returncode == 0:
            return
        alive = docker_run(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False)
        if alive.returncode != 0 or alive.stdout.strip() != "true":
            logs = docker_run(["docker", "logs", name], check=False)
            raise ConfinementError(f"broker container exited at startup: {(logs.stdout or logs.stderr or '')[-300:]}")
        time.sleep(0.25)
    raise ConfinementError(f"broker socket did not appear within {timeout_s:.0f}s")


def teardown(*, agent_name: str, broker_name: str, volume: str) -> None:
    """Both containers down, the volume gone. Idempotent: every step tolerates the prior one."""
    for name in (agent_name, broker_name):
        docker_run(["docker", "rm", "-f", name], check=False, timeout=90)
    docker_run(["docker", "volume", "rm", volume], check=False)


def _copy_modules(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent
    for module in MODULES:
        shutil.copyfile(source / module, target / module)


def _materialize_secrets(spec: ConfinementSpec, secrets_dir: Path) -> dict[str, Path]:
    """Readable copies of the credential files, one per upstream, inside the run's temp root.

    The copies exist only to give both containers one stable mount source with no operator
    directory mounted (ADR-0074, condition 1: single read-only files, never a directory). For the
    TypeSafe key the copy is byte-identical to the operator's file; for the model provider the
    harness wrote a scoped file holding only the arm's one key value before building the spec.
    """
    secrets_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    for upstream in spec.upstreams:
        target = secrets_dir / f"{upstream.name}.key"
        target.write_text(Path(upstream.credential_file).read_text(encoding="utf-8-sig"), encoding="utf-8")
        files[upstream.name] = target
    return files


@contextmanager
def run_confined(
    command: AgentCommand,
    *,
    spec: ConfinementSpec,
    mcp_config: Callable[[Path], Mapping[str, Any]] | Mapping[str, Any],
    secret: str,
    run_dir: Path,
    prepare: Callable[[Path], None] | None = None,
    parse: Callable[[Sequence[str]], stream.Trace] | None = None,
    scratch_files: Callable[[Path], None] | None = None,
) -> Generator[AgentRunResult, None, None]:
    """Run the agent once inside the container boundary; yields the result while the workdir exists.

    The same contract as `evals.agent.run_agent` — records in `run_dir`, a transcript in
    `stream.jsonl`, the relay log copied out, the escape canary over the transcript, and the
    after-run secret scan that fails the run on a hit — with the boundary provided by the two
    containers instead of the macOS sandbox. `command.argv` receives the CONTAINER config path
    (`/scratch/agent-cfg/mcp.json`); the `mcp_config` callable receives the HOST scratch directory,
    writes its files there, and emits container-side paths. On any exit path — timeout, crash,
    success — both containers are removed, the socket volume is deleted, and the grant file is gone
    with the run's temp root.
    """
    if not secret:
        raise ValueError("run_confined needs the non-empty secret to scrub")
    for key, value in spec.env.items():
        if secret in value:
            raise ConfinementError(f"the secret value must not ride in the container env ({key})")
    run_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="jev-agent-work-"))
    root = Path(tempfile.mkdtemp(prefix="jev-confined-"))
    scratch = root / "scratch"
    grants = root / "grants"
    for directory in (scratch / "home", scratch / "agent", scratch / "tmp", scratch / "agent-cfg", grants):
        directory.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:12]
    volume = f"jev-eval-sock-{tag}"
    broker_name = f"jev-eval-broker-{tag}"
    agent_name = f"jev-eval-agent-{tag}"
    try:
        if prepare is not None:
            prepare(workdir)
        if scratch_files is not None:
            scratch_files(scratch)
        # The agent-visible MCP config: written host-side into the scratch, read at /scratch inside.
        document = mcp_config(scratch) if callable(mcp_config) else mcp_config
        config = scratch / "agent-cfg" / "mcp.json"
        config.write_text(json.dumps(document), encoding="utf-8")
        config.chmod(0o600)
        if spec.adapter_source:
            adapter = scratch / "adapter"
            adapter.mkdir(exist_ok=True)
            shutil.copyfile(spec.adapter_source, adapter / "index.ts")
        _copy_modules(code := root / "code")
        _copy_modules(scratch / "code")
        credential_files = _materialize_secrets(spec, root / "secrets")
        mounts = {upstream.name: f"{CONTAINER_SECRETS}/{upstream.name}.key" for upstream in spec.upstreams}
        config_path = root / "broker-config.json"
        config_path.write_text(json.dumps(broker_config(spec.upstreams, secret_mounts=mounts)), encoding="utf-8")
        capability = mint_capability(grants, spec.timeout_s + CAPABILITY_MARGIN_S)
        (scratch / "entrypoint.sh").write_text(
            entrypoint_text(spec, command.argv(Path(AGENT_CONFIG))), encoding="utf-8"
        )
        (scratch / "entrypoint.sh").chmod(0o755)
        docker_run(["docker", "volume", "create", volume])
        docker_run(
            broker_argv(
                spec,
                name=broker_name,
                volume=volume,
                grants_dir=grants,
                config_path=config_path,
                code_dir=code,
                credential_files=credential_files,
            )
        )
        wait_for_broker(broker_name)
        argv = agent_argv(
            spec, name=agent_name, volume=volume, workdir=workdir, scratch=scratch, grant_file=capability.grant_path
        )
        box = container_boundary(dict(spec.env))
        started = time.perf_counter()
        stdout, stderr, returncode = capture_output(
            argv, cwd=workdir, env=docker_env(), timeout_s=command.timeout_s, cap=STDOUT_CAP_BYTES
        )
        wall = time.perf_counter() - started
        for name in ("jev-calls.jsonl", "jev-calls.stderr"):
            relay = scratch / name
            if relay.is_file():
                shutil.copyfile(relay, run_dir / name)
        write_run_logs(run_dir, stdout, stderr)
        trace = (parse or stream.parse)(stdout.splitlines())
        escape = escape_scan(stdout, box)
        hit = secret_scan(run_dir, secret)
        if hit:
            prefix = f"{escape}; " if escape else ""
            escape = f"{prefix}secret scan: key value found in {', '.join(hit)}"
        yield AgentRunResult(
            argv=tuple(argv),
            workdir=workdir,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            wall_s=wall,
            trace=trace,
            status=run_status(returncode, trace, command.timeout_s),
            escape=escape or None,
        )
    finally:
        teardown(agent_name=agent_name, broker_name=broker_name, volume=volume)
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)
        secret_scrub(run_dir, secret)


def probe_provider(spec: ConfinementSpec, *, timeout_s: float = 60.0) -> str | None:
    """The confined preflight: one allowlisted provider request through the real boundary.

    Runs a probe container with the agent image, `--network none`, the socket volume, and a fresh
    capability — exactly the agent's egress path — and asks the broker for the provider's base
    path. Any framed answer that is not a capability or allowlist refusal proves the chain; returns
    None on success or the refusal detail.
    """
    provider = next((upstream for upstream in spec.upstreams if upstream.name != "typesafe"), None)
    if provider is None:
        return "no model-provider upstream is configured"
    root = Path(tempfile.mkdtemp(prefix="jev-probe-"))
    try:
        grants = root / "grants"
        code = root / "code"
        _copy_modules(code)
        capability = mint_capability(grants, timeout_s)
        credential_files = _materialize_secrets(spec, root / "secrets")
        mounts = {upstream.name: f"{CONTAINER_SECRETS}/{upstream.name}.key" for upstream in spec.upstreams}
        config_path = root / "broker-config.json"
        config_path.write_text(json.dumps(broker_config(spec.upstreams, secret_mounts=mounts)), encoding="utf-8")
        tag = uuid.uuid4().hex[:12]
        volume = f"jev-eval-sock-{tag}"
        broker_name = f"jev-eval-broker-{tag}"
        probe_name = f"jev-eval-probe-{tag}"
        probe = root / "probe.py"
        probe.write_text(
            _PROBE_SCRIPT.format(scheme=provider.scheme, host=provider.host, port=provider.port), encoding="utf-8"
        )
        try:
            docker_run(["docker", "volume", "create", volume])
            docker_run(
                broker_argv(
                    spec,
                    name=broker_name,
                    volume=volume,
                    grants_dir=grants,
                    config_path=config_path,
                    code_dir=code,
                    credential_files=credential_files,
                )
            )
            wait_for_broker(broker_name)
            argv = [
                "docker",
                "run",
                "--rm",
                "--name",
                probe_name,
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-v",
                f"{volume}:/broker:ro",
                "-v",
                f"{capability.grant_path}:{AGENT_GRANT_MOUNT}:ro",
                "-v",
                f"{code}:/code:ro",
                "-v",
                f"{probe}:/probe.py:ro",
                spec.agent_image,
                "python3",
                "/probe.py",
            ]
            result = docker_run(argv, check=False, timeout=timeout_s)
        finally:
            teardown(agent_name=probe_name, broker_name=broker_name, volume=volume)
        if result.returncode == 0:
            return None
        return ((result.stdout or "").strip() or (result.stderr or "").strip())[-200:]
    finally:
        shutil.rmtree(root, ignore_errors=True)


_PROBE_SCRIPT = """
import json, socket, sys
sys.path.insert(0, "/code")
import protocol
cap = json.load(open("/run/capability.json"))["token"]
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
    sock.settimeout(30)
    sock.connect("/broker/broker.sock")
    protocol.send_frame(
        sock,
        {{
            "cap": cap,
            "method": "GET",
            "scheme": "{scheme}",
            "host": "{host}",
            "port": {port},
            "path": "/",
            "headers": [],
        }},
        b"",
    )
    header, _body = protocol.recv_frame(sock)
if "error" in header:
    sys.stderr.write(str(header.get("detail", "refused")) + "\n")
    raise SystemExit(1)
"""
