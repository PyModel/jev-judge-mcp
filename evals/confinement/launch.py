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
import stat
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

BROKER_LIFE_MARGIN_S = 300.0
"""Broker self-deadline past the run/probe timeout (F5): teardown slack for a slow container
stop, then the sidecar exits and its key mounts go away even if the harness died."""

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

CONTAINER_ADAPTER = "/usr/local/lib/node_modules/pi-mcp-adapter/index.ts"
"""The adapter ships inside the image (F2), so the confined argv names this fixed path."""

MODULES = ("protocol.py", "broker.py", "shim.py")
"""The stdlib-only confinement modules copied into both containers at run time."""

CLOCK = time.time
"""Injectable now, so grant expiry tests need no sleeping."""


class ConfinementError(RuntimeError):
    """The confinement boundary could not be built or verified. Not a run: nothing is booked."""


OPENAI_ENDPOINTS = ("/chat/completions", "/models")
"""An openai-completions provider's whole API surface (F3): the chat endpoint and the model list."""
ANTHROPIC_ENDPOINTS = ("/v1/messages", "/v1/messages/count_tokens", "/v1/models")
"""Claude's whole API surface: messages, token counting, and the model list."""


@dataclass(frozen=True)
class Upstream:
    """One allowlisted egress destination, and the credential the broker injects for it."""

    name: str
    scheme: str
    host: str
    port: int
    methods: tuple[str, ...]
    paths: tuple[str, ...]
    """Exact origin-form endpoint paths, joined onto `base_path` when forwarding (no wildcards)."""
    base_path: str = ""
    """The provider base URL's path (`/zen/go/v1`), kept and prepended to every forwarded path."""
    credential_file: str = ""
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
            base_path="",
            credential_file=credential_file,
            shim_port=shim_port,
        )

    @classmethod
    def provider(
        cls, name: str, base_url: str, credential_file: str, api: str = "", shim_port: int = 8080
    ) -> "Upstream":
        """The agent's model provider: host, base path, and its exact endpoint list (F3).

        The base URL's path prefix (e.g. `/zen/go/v1`) is kept and prepended to every forwarded
        path, and the endpoint list replaces the old `*`: the broker forwards only the paths the
        provider's API family defines, each with its real method."""
        parts = urlsplit(base_url if "://" in base_url else f"https://{base_url}")
        if not parts.hostname:
            raise ConfinementError(f"provider base URL has no host: {base_url}")
        anthropic = "anthropic" in api.lower()
        endpoints = ANTHROPIC_ENDPOINTS if anthropic else OPENAI_ENDPOINTS
        return cls(
            name=name,
            scheme=parts.scheme or "https",
            host=parts.hostname,
            port=parts.port or (80 if parts.scheme == "http" else 443),
            methods=("POST", "GET"),
            paths=endpoints,
            base_path=parts.path.rstrip("/"),
            credential_file=credential_file,
            credential_header="x-api-key" if anthropic else "Authorization",
            credential_scheme="" if anthropic else "Bearer ",
            shim_port=shim_port,
        )

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def full_path(self, path: str) -> str:
        """The forwarded path: base path plus the origin-form endpoint path."""
        return f"{self.base_path}{path}"


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


def broker_config(
    upstreams: Sequence[Upstream],
    *,
    secret_mounts: Mapping[str, str],
    max_life_s: float = 4 * 3600.0,
) -> dict[str, Any]:
    """The broker container's config document. `secret_mounts` maps upstream name → container path;
    `max_life_s` is the sidecar's self-bound deadline (F5), long enough for a full study, short
    enough that a killed run's key mounts cannot outlive the day."""
    return {
        "listen": CONTAINER_SOCKET,
        "grants_dir": CONTAINER_GRANTS,
        "max_lifetime_s": max_life_s,
        "upstreams": [
            {
                "name": upstream.name,
                "scheme": upstream.scheme,
                "host": upstream.host,
                "port": upstream.port,
                "methods": list(upstream.methods),
                "paths": list(upstream.paths),
                "base_path": upstream.base_path,
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
        # F4: repr(tuple) is a valid tuple literal for any port count, and a shim that never
        # listens is fatal — the agent must not start against an unbound loopback.
        lines += [
            "python3 -c 'import socket, time, sys",
            f"for port in {tuple(ports)!r}:",
            "    for _ in range(200):",
            "        try:",
            '            s = socket.create_connection(("127.0.0.1", port), 0.5); s.close(); break',
            "        except OSError:",
            "            time.sleep(0.05)",
            "    else:",
            '            sys.exit(f"shim on {port} never listened")\' || exit 97',
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


OWNER_LABEL = "jev-eval"
"""Every container and volume the boundary creates carries this label; the reaper matches it."""

REAP_STALE_S = 2 * 3600.0
"""A temp dir younger than this may belong to a live run, so the reaper leaves it alone. Runs are
bounded by the study timeout; only orphans of a killed harness outlive this."""


def exit_on_sigterm() -> None:
    """SIGTERM raises SystemExit, so `finally` blocks and context managers unwind (F5): teardown
    runs instead of the default terminate-on-the-spot, which leaked every container and mount."""
    import signal

    def handler(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, handler)


def reap() -> dict[str, list[str]]:
    """Remove a killed run's leftovers (F5): labeled containers and volumes, and stale run temp
    dirs. Called before a confined study starts; returns what it removed."""
    removed: dict[str, list[str]] = {"containers": [], "volumes": [], "dirs": []}
    if not shutil.which("docker"):
        return removed  # no docker CLI: no containers or volumes can exist to reap
    for name in docker_run(["docker", "ps", "-aq", "--filter", f"label={OWNER_LABEL}"], check=False).stdout.split():
        if docker_run(["docker", "rm", "-f", name], check=False).returncode == 0:
            removed["containers"].append(name)
    for name in docker_run(
        ["docker", "volume", "ls", "-q", "--filter", f"label={OWNER_LABEL}"], check=False
    ).stdout.split():
        if docker_run(["docker", "volume", "rm", name], check=False).returncode == 0:
            removed["volumes"].append(name)
    now = time.time()
    for prefix in ("jev-confined-", "jev-probe-", "jev-ab-secrets-", "jev-agent-work-", "jev-grade-"):
        for directory in Path(tempfile.gettempdir()).glob(f"{prefix}*"):
            try:
                if directory.is_dir() and now - directory.stat().st_mtime > REAP_STALE_S:
                    shutil.rmtree(directory, ignore_errors=True)
                    removed["dirs"].append(directory.name)
            except OSError:
                continue
    return removed


def copy_regular_nofollow(source: Path, target: Path) -> bool:
    """Copy `source` to `target` only if it is a regular file reached without following a symlink
    (F1): an agent-writable tree cannot make the host read anything but the file it named."""
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        with os.fdopen(fd, "rb") as reader:
            fd = -1
            with open(target, "wb") as writer:
                while chunk := reader.read(65_536):
                    writer.write(chunk)
        return True
    finally:
        if fd >= 0:
            os.close(fd)


def _copy_modules(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent
    for module in MODULES:
        shutil.copyfile(source / module, target / module)


def credential_files(spec: ConfinementSpec) -> dict[str, Path]:
    """The operator's own credential files, mounted read-only into the broker container exactly
    as they stand (F10): no copy is made, so no copy can linger at the wrong mode or outlive the
    run. The provider file is scoped before the spec is built (one provider's key, never the
    operator's whole auth.json)."""
    return {upstream.name: Path(upstream.credential_file) for upstream in spec.upstreams}


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
    extra_secrets: Sequence[str] = (),
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
    secrets = [secret, *[value for value in extra_secrets if value]]
    for key, value in spec.env.items():
        if any(one in value for one in secrets):
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
            source = Path(spec.adapter_source)
            adapter = scratch / "adapter"
            if source.is_dir():
                # F2: the adapter is a package (index.ts imports siblings); the whole directory
                # crosses read-only-mounted at runtime, never a lone entry file.
                shutil.copytree(
                    source, adapter, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "node_modules")
                )
            else:
                adapter.mkdir(exist_ok=True)
                shutil.copyfile(source, adapter / "index.ts")
        _copy_modules(code := root / "code")
        _copy_modules(scratch / "code")
        credentials = credential_files(spec)
        mounts = {upstream.name: f"{CONTAINER_SECRETS}/{upstream.name}.key" for upstream in spec.upstreams}
        config_path = root / "broker-config.json"
        config_path.write_text(
            json.dumps(
                broker_config(spec.upstreams, secret_mounts=mounts, max_life_s=spec.timeout_s + BROKER_LIFE_MARGIN_S)
            ),
            encoding="utf-8",
        )
        capability = mint_capability(grants, spec.timeout_s + CAPABILITY_MARGIN_S)
        (scratch / "entrypoint.sh").write_text(
            entrypoint_text(spec, command.argv(Path(AGENT_CONFIG))), encoding="utf-8"
        )
        (scratch / "entrypoint.sh").chmod(0o755)
        docker_run(["docker", "volume", "create", "--label", OWNER_LABEL, volume])
        docker_run(
            broker_argv(
                spec,
                name=broker_name,
                volume=volume,
                grants_dir=grants,
                config_path=config_path,
                code_dir=code,
                credential_files=credentials,
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
            copy_regular_nofollow(scratch / name, run_dir / name)
        write_run_logs(run_dir, stdout, stderr)
        trace = (parse or stream.parse)(stdout.splitlines())
        escape = escape_scan(stdout, box)
        hits = sorted({name for value in secrets for name in secret_scan(run_dir, value)})
        if hits:
            prefix = f"{escape}; " if escape else ""
            escape = f"{prefix}secret scan: key value found in {', '.join(hits)}"
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
        for value in secrets:
            secret_scrub(run_dir, value)


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
        credentials = credential_files(spec)
        mounts = {upstream.name: f"{CONTAINER_SECRETS}/{upstream.name}.key" for upstream in spec.upstreams}
        config_path = root / "broker-config.json"
        config_path.write_text(
            json.dumps(
                broker_config(spec.upstreams, secret_mounts=mounts, max_life_s=timeout_s + BROKER_LIFE_MARGIN_S)
            ),
            encoding="utf-8",
        )
        tag = uuid.uuid4().hex[:12]
        volume = f"jev-eval-sock-{tag}"
        broker_name = f"jev-eval-broker-{tag}"
        probe_name = f"jev-eval-probe-{tag}"
        probe = root / "probe.py"
        models_path = next((path for path in provider.paths if path.endswith("/models")), provider.paths[0])
        probe.write_text(
            _PROBE_SCRIPT.format(scheme=provider.scheme, host=provider.host, port=provider.port, path=models_path),
            encoding="utf-8",
        )
        try:
            docker_run(["docker", "volume", "create", "--label", OWNER_LABEL, volume])
            docker_run(
                broker_argv(
                    spec,
                    name=broker_name,
                    volume=volume,
                    grants_dir=grants,
                    config_path=config_path,
                    code_dir=code,
                    credential_files=credentials,
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
            "path": "{path}",
            "headers": [],
        }},
        b"",
    )
    header, _body = protocol.recv_frame(sock)
if "error" in header:
    sys.stderr.write(str(header.get("detail", "refused")) + "\\n")
    raise SystemExit(1)
status = int(header.get("status", 0))
if status >= 500:
    sys.stderr.write(f"provider unhappy (HTTP {{status}})\\n")
    raise SystemExit(1)
if status in (401, 403):
    sys.stderr.write(f"credential refused (HTTP {{status}})\\n")
    raise SystemExit(1)
if status == 404:
    sys.stderr.write("probe endpoint {path!r} not found (HTTP 404): the entry base URL or API family is wrong\\n")
    raise SystemExit(1)
if status >= 400:
    sys.stderr.write(f"provider refused the probe (HTTP {{status}})\\n")
    raise SystemExit(1)
"""

GRADE_CODE = ("__init__.py", "grade.py", "tasks.py")
"""The grading slice of evals.ab, copied read-only into the grading container with the fixture."""


def _copy_grade_code(target: Path) -> None:
    """`evals.ab`'s grading modules plus the fixture, so the container grades with the repo's own
    code and its own fixture paths (tasks.py resolves the fixture next to itself)."""
    package = Path(__file__).resolve().parents[1] / "ab"
    (target / "evals").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(package.parent / "__init__.py", target / "evals" / "__init__.py")
    ab = target / "evals" / "ab"
    ab.mkdir(exist_ok=True)
    for name in GRADE_CODE:
        shutil.copyfile(package / name, ab / name)
    shutil.copytree(
        package / "fixture", ab / "fixture", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__")
    )


_GRADE_DRIVER = """
import json, os, subprocess, sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, "/code")
from evals.ab import tasks
from evals.ab.grade import grade, expected_ids

# Hostile-tree-safe git (F1): no config files, no fsmonitor, no hooks, no protocol helpers.
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def git(*args):
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.pager=cat", "-c", "core.hooksPath=", *args],
        cwd="/tree", env=GIT_ENV, capture_output=True, text=True, check=False,
    )


mode, argument = sys.argv[1], sys.argv[2]
if mode == "reference":
    task = tasks.load_task(argument)
    ids = expected_ids(task, sys.executable)
    Path("/out/reference.json").write_text(json.dumps({"task": task.id, "ids": list(ids)}))
    raise SystemExit(0)

task = tasks.load_task(argument)
if (Path("/tree") / ".git").exists():
    git("add", "-A")
    Path("/out/diff.patch").write_text(git("diff", "--cached").stdout, encoding="utf-8")
else:
    Path("/out/diff.patch").write_text("", encoding="utf-8")
result = grade(Path("/tree"), task, sys.executable)
Path("/out/grade.json").write_text(json.dumps(asdict(result)))
"""


def _grade_container(
    image: str,
    *,
    mode: str,
    argument: str,
    tree: Path | None,
    timeout_s: float,
) -> dict[str, Any]:
    """Run the grading driver once inside the agent image (F1): `--network none`,
    `--cap-drop ALL`, only the tree copy, the grading code, and an output dir mounted. Returns
    the files the driver wrote."""
    import tempfile
    import uuid

    root = Path(tempfile.mkdtemp(prefix="jev-grade-"))
    out = root / "out"
    out.mkdir()
    _copy_modules(root / "code")
    _copy_grade_code(root / "code")
    (root / "code" / "grade_driver.py").write_text(_GRADE_DRIVER, encoding="utf-8")
    argv = [
        "docker",
        "run",
        "--rm",
        "--name",
        f"jev-eval-grade-{uuid.uuid4().hex[:12]}",
        "--label",
        OWNER_LABEL,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "-v",
        f"{root / 'code'}:/code:ro",
        "-v",
        f"{out}:/out",
    ]
    if tree is not None:
        # The tree copy is mounted writable: `git add` updates the snapshot repo the tree carries,
        # and any write the graded code makes lands in this throwaway copy, never on the host.
        argv += ["-v", f"{tree}:/tree"]
    argv += [image, "python3", "/code/grade_driver.py", mode, argument]
    try:
        done = docker_run(argv, check=False, timeout=timeout_s)
        if done.returncode != 0:
            raise ConfinementError(f"grading container failed: {(done.stderr or '')[-300:]}")
        written: dict[str, Any] = {}
        for name in ("diff.patch", "grade.json", "reference.json"):
            path = out / name
            if path.is_file():
                written[name] = path.read_text(encoding="utf-8")
        if not written:
            raise ConfinementError("grading container wrote no output")
        return written
    finally:
        shutil.rmtree(root, ignore_errors=True)


def confined_postprocess(image: str, workdir: Path, task_id: str, *, timeout_s: float = 600.0) -> tuple[str, Any]:
    """The confined path's post-run step (F1): diff and grade the agent's tree INSIDE the image,
    never on the host. The tree crosses as a symlink-preserving copy; nothing in it executes with
    host privileges. Returns `(patch_text, Grade)`.
    """
    import tempfile

    from evals.ab.grade import AddedTest, Grade

    root = Path(tempfile.mkdtemp(prefix="jev-grade-"))
    try:
        tree = root / "tree"
        shutil.copytree(workdir, tree, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
        written = _grade_container(image, mode="grade", argument=task_id, tree=tree, timeout_s=timeout_s)
        payload = json.loads(written["grade.json"])
        added = tuple(
            AddedTest(file=item["file"], name=item["name"], outcome=item["outcome"], relevant=item["relevant"])
            for item in payload["added_tests"]
        )
        grade_result = Grade(
            acceptance_passed=payload["acceptance_passed"],
            acceptance_total=payload["acceptance_total"],
            original_passed=payload["original_passed"],
            original_total=payload["original_total"],
            regressions=tuple(payload["regressions"]),
            protected_changed=tuple(payload["protected_changed"]),
            preexisting_altered=tuple(payload["preexisting_altered"]),
            added_tests=added,
        )
        return written.get("diff.patch", ""), grade_result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def confined_reference(image: str, task_id: str, *, timeout_s: float = 600.0) -> tuple[str, ...]:
    """Reference grading on the same interpreter the agent runs on (F1): the task's reference
    solution graded inside the image. Raises when the reference does not pass, exactly like the
    host-side `expected_ids` it replaces for confined studies."""
    written = _grade_container(image, mode="reference", argument=task_id, tree=None, timeout_s=timeout_s)
    return tuple(json.loads(written["reference.json"])["ids"])
