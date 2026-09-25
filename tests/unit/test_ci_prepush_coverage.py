"""The pre-push gate cannot drift from the workflow it mirrors, and it must chain (ADR-0056).

The gate runs every command the GitHub `ci` workflow runs — natively (`make ci`) and on
Linux (scripts/ci/linux_check.sh, in the digest-pinned image from
docker/ci-linux.Dockerfile) — against a clean temporary clone of each pushed commit. These
tests hold three contracts:

1. Workflow parity: every ci.yml `uses:` and `run:` is emulated by a gate leg, with the
   workflow's own version pins (Node, the old-Python entry) read from ci.yml and verified
   against the machinery; unknown workflow shapes fail closed.
2. Behavior, at the real boundaries: the chain runs in throwaway repos with isolated global
   config; the native leg, its time bound, the pushed-commit clone, and the image-tag rule
   run the real scripts against stubs.
3. Money and cleanup: no paid stage or live flag in the machinery, no unbounded stage, no
   leftover clone or container.

`make hooks` enables the gate by installing forwarders outside the tracked tree (see
scripts/ci/install_hooks.sh); every client-side hook in githooks(5) is offered there except
reference-transaction, which is excluded on purpose: it fires on every ref transaction —
client side too — and a forwarder there costs one bash spawn per ref update for no gate
value (ADR-0056).
"""

import os
import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
MAKEFILE = REPO / "Makefile"
HOOK_CHAIN = REPO / "scripts" / "ci" / "hook_chain.sh"
LINUX_CHECK = REPO / "scripts" / "ci" / "linux_check.sh"
PRE_PUSH_CHECK = REPO / "scripts" / "ci" / "pre_push_check.sh"
INSTALL_HOOKS = REPO / "scripts" / "ci" / "install_hooks.sh"
DOCKERFILE = REPO / "docker" / "ci-linux.Dockerfile"

# The `uses:` steps the gate emulates: checkout (the temporary clone), setup-uv (uv baked
# into the image), setup-node (Node baked in, asserted per check), setup-python 3.10 (the
# smoke job's old-Python entry guard), and upload-artifact (the build stage produces dist/
# in the container; only the upload itself is CI-side plumbing).
ALLOWED_USES = {
    "actions/checkout@v4",
    "astral-sh/setup-uv@v6",
    "actions/setup-node@v4",
    "actions/setup-python@v5",
    "actions/upload-artifact@v4",
}
# Non-make ci.yml run commands, mapped to the substring that must appear in a Linux stage's
# command. Anything else fails: an unclassified workflow command cannot be checked.
ALLOWED_RUN = {
    "uv sync --locked --all-extras": "uv sync --locked --all-extras",
    "python scripts/ci_old_python_entry.py": "scripts/ci_old_python_entry.py",
}
PAID_TARGETS = ("security-live", "eval-live", "ab", "load")
PAID_FLAGS = ("JEV_EVAL_LIVE", "JEV_AB_LIVE")

# Every client-side hook name in githooks(5) except reference-transaction, which fires on
# every ref transaction (client side too) and is excluded on purpose: a forwarder there
# costs one bash spawn per ref update for no gate value (ADR-0056).
CLIENT_HOOKS = (
    "applypatch-msg",
    "pre-applypatch",
    "post-applypatch",
    "pre-commit",
    "pre-merge-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "pre-rebase",
    "post-checkout",
    "post-merge",
    "pre-push",
    "pre-auto-gc",
    "post-rewrite",
    "sendemail-validate",
    "fsmonitor-watchman",
    "p4-changelist",
    "p4-prepare-changelist",
    "p4-post-changelist",
    "p4-pre-submit",
    "post-index-change",
)
DELETION_ONLY_STDIN = b"refs/heads/gone " + b"0" * 40 + b" refs/heads/gone " + b"0" * 40 + b"\n"


@dataclass
class Step:
    """One workflow step: an action ref, a `run` command, and any `with:` values."""

    action: str | None = None
    run: str | None = None
    with_values: dict[str, str] = field(default_factory=lambda: dict[str, str]())


def _parse_steps() -> list[Step]:
    """Every step of every ci.yml job, or a failure naming the unrecognized line.

    Recognizes exactly the shapes ci.yml uses today; anything else (a new key, a block
    scalar, a new nesting level) is an error, because the gate cannot know what it means.
    """
    steps: list[Step] = []
    in_jobs = False
    in_with = False
    block: list[str] | None = None

    def unknown(lineno: int, line: str, reason: str) -> None:
        pytest.fail(f"ci.yml:{lineno}: unrecognized {reason}: {line!r}")

    for lineno, raw in enumerate(WORKFLOW.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            if block is not None and len(line) - len(line.lstrip(" ")) >= 10:
                block.append("")
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if block is not None:
            if indent >= 10:
                block.append(content)
                continue
            steps[-1].run = "\n".join(block)
            block = None
        if indent == 0:
            in_jobs = content == "jobs:"
            continue
        if not in_jobs:
            continue
        if indent == 2:
            if not content.endswith(":") or ":" in content[:-1]:
                unknown(lineno, line, "job key")
            continue
        if indent == 4:
            if content == "steps:" or content.startswith(("runs-on:", "needs:")):
                continue
            unknown(lineno, line, "job key")
        if indent == 6 and content.startswith("- "):
            item = content[2:]
            if item.startswith("uses:"):
                steps.append(Step(action=item[len("uses:") :].strip()))
                in_with = False
                continue
            if item.startswith("name:"):
                continue
            if item.startswith("run:"):
                scalar = item[len("run:") :].strip()
                if scalar in ("|", ">", ">-", "|-"):
                    steps.append(Step(run=""))
                    block = []
                    in_with = False
                    continue
                steps.append(Step(run=scalar))
                in_with = False
                continue
            unknown(lineno, line, "step")
        if indent == 8 and (content == "with:" or content.startswith("run:")):
            if content == "with:":
                in_with = True
                continue
            # A block-scalar run under `- name:`: the step key sits at this indent.
            scalar = content[len("run:"):].strip()
            if scalar in ("|", ">", ">-", "|-"):
                steps.append(Step(run=""))
                block = []
                in_with = False
                continue
            steps.append(Step(run=scalar))
            in_with = False
            continue
        if in_with and indent == 10:
            key, sep, value = content.partition(":")
            if not sep or not steps:
                unknown(lineno, line, "with key")
            steps[-1].with_values[key.strip()] = value.strip()
            continue
        unknown(lineno, line, "line")
    if block is not None and steps:
        steps[-1].run = "\n".join(block)
    return steps


def _ci_make_targets(steps: list[Step]) -> set[str]:
    """The make targets ci.yml runs, including inside docker-run block scalars."""
    targets: set[str] = set()
    for step in steps:
        if step.run is None:
            continue
        if step.run in ALLOWED_RUN:
            continue
        if not step.run.startswith("make "):
            # Inside a docker-run block only a chained or line-initial `make` is an
            # invocation; `apt-get install make git` is a package list, not a target.
            for target in re.findall(r"(?:^|&&)\s*make (\S+)", step.run, re.MULTILINE):
                targets.add(target.rstrip("\"'),;"))
            continue
        targets.update(step.run[len("make ") :].split())
    return targets


def _linux_stage_targets() -> set[str]:
    """The make targets scripts/ci/linux_check.sh runs, from its own run lines."""
    return set(re.findall(r"^run make:(\S+) ", LINUX_CHECK.read_text(encoding="utf-8"), re.MULTILINE))


def _linux_stage_commands() -> list[str]:
    """The full command line of every Linux stage, from the runner's own run lines."""
    text = LINUX_CHECK.read_text(encoding="utf-8")
    return [match.group(1).strip() for match in re.finditer(r"^run \S+ \d+ (.+)$", text, re.MULTILINE)]


def _makefile_ci_targets() -> set[str]:
    line = next(line for line in MAKEFILE.read_text(encoding="utf-8").splitlines() if line.startswith("ci:"))
    return set(line[len("ci:") :].split())


def _workflow_pins() -> tuple[str, str]:
    """The workflow's own Node and Python pins, read from ci.yml — never restated here."""
    node = python = None
    for step in _parse_steps():
        if "node-version" in step.with_values:
            node = step.with_values["node-version"].strip('"')
        if "python-version" in step.with_values:
            python = step.with_values["python-version"].strip('"')
    assert node and python, "ci.yml lost its Node or Python pin; the gate has nothing to match"
    return node, python


def test_every_workflow_step_is_emulated() -> None:
    steps = _parse_steps()
    assert steps, "no steps parsed from ci.yml; the parser's shape assumptions broke"
    node_pin, python_pin = _workflow_pins()
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    linux = LINUX_CHECK.read_text(encoding="utf-8")
    for step in steps:
        if step.action is not None:
            assert step.action in ALLOWED_USES, f"ci.yml runs {step.action!r}, which the gate cannot emulate"
        if "node-version" in step.with_values:
            # The image downloads this exact release, and the runner asserts it per check:
            # a floating 24.x drifts ICU rendering, which is what parity grounds on.
            assert f"node-v{node_pin}-linux" in dockerfile, f"the image must ship Node {node_pin}, the workflow's pin"
            assert f'"v{node_pin}"' in linux, f"the runner must assert the workflow's Node pin {node_pin}"
        if "python-version" in step.with_values:
            assert f"uv python find --no-project {python_pin}" in linux, (
                f"the old-Python entry needs Python {python_pin} first on PATH, found with --no-project"
            )


def test_every_workflow_command_runs_in_a_gate_leg() -> None:
    steps = _parse_steps()
    linux = LINUX_CHECK.read_text(encoding="utf-8")
    ci_targets = _ci_make_targets(steps)
    assert ci_targets == _linux_stage_targets(), (
        "ci.yml's make targets and the gate's Linux stages differ; teach scripts/ci/linux_check.sh in the same commit"
    )
    assert ci_targets <= _makefile_ci_targets(), (
        "ci.yml runs a make target outside `make ci`, so the native leg misses it"
    )
    stage_commands = _linux_stage_commands()
    for step in steps:
        run = step.run
        if run is None or run.startswith("make "):
            continue
        if "docker run" in run:
            # A docker-run job must name its mirror in the leg: today that is the one-CPU
            # security stage, non-root, in the same digest-pinned image the workflow pins.
            assert "security-one-cpu" in linux, "a docker-run job must have its mirror stage in the leg"
            assert "--cpus 1" in linux, "the one-CPU mirror must carry the job's --cpus 1 quota"
            continue
        needle = ALLOWED_RUN.get(run or "")
        assert needle is not None, f"ci.yml runs {run!r}, which no gate leg is known to run"
        assert any(needle in command for command in stage_commands), f"no Linux stage runs anything like {needle!r}"


def test_native_leg_runs_sync_and_make_ci() -> None:
    text = PRE_PUSH_CHECK.read_text(encoding="utf-8")
    assert "uv sync --locked --all-extras" in text, "the native leg must sync, as every ci.yml job does"
    assert re.search(r"make -C \"\$TMP_DIR/src\" ci\b", text), (
        "the native leg must run `make ci` from the pushed commit's Makefile"
    )
    assert "linux_check.sh" in text, "the native leg alone is not the gate: the Linux leg must also run"
    assert "checkout --quiet --detach" in text, "the check must run a detached clean clone of the pushed commit"


def test_every_linux_stage_is_time_bounded() -> None:
    for line in LINUX_CHECK.read_text(encoding="utf-8").splitlines():
        if re.match(r"^run \S+ ", line):
            assert re.match(r"^run \S+ \d+ ", line), f"stage without a finite timeout: {line!r}"


def test_container_base_images_are_digest_pinned() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    froms = [line for line in text.splitlines() if line.startswith("FROM ")]
    assert froms, "no base image; the container is not pinned"
    for line in froms:
        assert "@sha256:" in line, f"base image not pinned by digest: {line!r}"
    copy_froms = re.findall(r"COPY --from=(\S+)", text)
    for image in copy_froms:
        assert "@sha256:" in image, f"copied image not pinned by digest: {image!r}"


def test_the_gate_never_runs_a_paid_stage_or_sets_a_live_flag() -> None:
    """The regression: the MACHINERY gaining a paid invocation outside ci.yml — a stage
    hardwired into a script's sync line, or a leg that shells into `make ab` — which the
    stage-equality check cannot see, because it only compares ci.yml against the stage list."""
    for script in (LINUX_CHECK, PRE_PUSH_CHECK, INSTALL_HOOKS, HOOK_CHAIN):
        text = script.read_text(encoding="utf-8")
        for target in PAID_TARGETS:
            assert not re.search(rf"^run .*make.*\b{target}\b", text, re.MULTILINE), (
                f"{script.name} must not run the paid {target} stage"
            )
        for flag in PAID_FLAGS:
            assert flag not in text, f"{script.name} must not set {flag}"
    for stage in _linux_stage_targets():
        assert stage not in PAID_TARGETS


@dataclass
class TempRepo:
    root: Path
    env: dict[str, str]
    hooks_dir: Path

    def run(
        self,
        argv: Sequence[Path | str],
        input: bytes = b"",
        env_extra: dict[str, str] | None = None,
        timeout: float = 120,
    ) -> subprocess.CompletedProcess[bytes]:
        env = dict(self.env)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [str(arg) for arg in argv],
            cwd=self.root,
            env=env,
            input=input,
            capture_output=True,
            timeout=timeout,
        )

    def git(self, *args: str) -> str:
        """Run git in the repo; assert success, return stripped stdout."""
        proc = self.run(("git", *args))
        assert proc.returncode == 0, proc.stderr.decode()
        return proc.stdout.decode().strip()


def _temp_gate_repo(tmp_path: Path, with_docker: bool = False) -> TempRepo:
    """A throwaway clone of the gate's files, hooks installed, config isolated.

    Every git call sees GIT_CONFIG_GLOBAL pointed at an empty temp file and
    GIT_CONFIG_NOSYSTEM set, so the machine's real global and system config never decides
    a test's outcome — and is never written to.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    shutil.copytree(REPO / "scripts" / "ci", repo / "scripts" / "ci")
    if with_docker:
        shutil.copytree(REPO / "docker", repo / "docker")
    gitconfig = tmp_path / "global.gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        HOME=str(home),
        GIT_CONFIG_GLOBAL=str(gitconfig),
        GIT_CONFIG_NOSYSTEM="1",
    )
    subprocess.run(("git", "init", "-q", str(repo)), capture_output=True, check=True, env=env)
    # The tests commit; the container and a fresh runner have no global identity.
    subprocess.run(
        ("git", "-C", str(repo), "config", "user.email", "gate@example.invalid"),
        capture_output=True,
        check=True,
        env=env,
    )
    subprocess.run(
        ("git", "-C", str(repo), "config", "user.name", "gate test"),
        capture_output=True,
        check=True,
        env=env,
    )
    subprocess.run(("bash", "scripts/ci/install_hooks.sh"), cwd=repo, env=env, capture_output=True, check=True)
    hooks_dir = (
        subprocess.run(("git", "-C", str(repo), "config", "core.hooksPath"), env=env, capture_output=True, check=True)
        .stdout.decode()
        .strip()
    )
    return TempRepo(root=repo, env=env, hooks_dir=Path(hooks_dir))


def _write_global_hook(repo: TempRepo, tmp_path: Path, name: str, body: str) -> Path:
    """A fake previous hook, registered as the global core.hooksPath for this repo."""
    global_hooks = tmp_path / "global-hooks"
    global_hooks.mkdir(exist_ok=True)
    hook = global_hooks / name
    hook.write_text(body, encoding="utf-8")
    hook.chmod(0o755)
    gitconfig = Path(repo.env["GIT_CONFIG_GLOBAL"])
    gitconfig.write_text(f"[core]\n\thooksPath = {global_hooks}\n", encoding="utf-8")
    return hook


def test_every_forwarded_hook_reaches_its_previous_hook(tmp_path: Path) -> None:
    """The forwarder census, behavioral: each enabled hook name must invoke the previous
    hook of its own name with the args it was given — one missing forwarder is one silently
    dead hook on this machine."""
    for name in CLIENT_HOOKS:
        case = tmp_path / name
        case.mkdir()
        repo = _temp_gate_repo(case)
        log = case / "hook.log"
        body = ('#!/usr/bin/env bash\nprintf "hook:%s args:%s\\n" "$0" "$*" >>LOG\nexit 0\n').replace("LOG", str(log))
        _write_global_hook(repo, case, name, body)
        proc = repo.run((repo.hooks_dir / name, "positional-arg"), input=b"")
        assert proc.returncode == 0, f"{name}: {proc.stderr.decode()[:300]}"
        logged = log.read_text(encoding="utf-8")
        assert logged.rstrip().endswith(f"/{name} args:positional-arg"), (
            f"{name} must invoke the previous {name} with its args, saw: {logged!r}"
        )


def test_chain_runs_a_previous_hook_with_a_non_bash_shebang(tmp_path: Path) -> None:
    """Git executes hooks directly; the chain must too, shebang and all.

    A previous hook that is a Python script dies with a bash syntax error when the chain
    reads it as shell text, and its real work never runs."""
    repo = _temp_gate_repo(tmp_path)
    log = tmp_path / "hook.log"
    body = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "with open('" + str(log) + "', 'w') as fh:\n"
        "    fh.write('args:' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.exit(7)\n"
    )
    _write_global_hook(repo, tmp_path, "commit-msg", body)
    proc = repo.run((repo.hooks_dir / "commit-msg", ".git/COMMIT_EDITMSG"), input=b"")
    assert proc.returncode == 7, f"the python hook must run as python: {proc.stderr.decode()[:400]}"
    assert log.exists() and "args:.git/COMMIT_EDITMSG" in log.read_text(encoding="utf-8")


def test_chain_runs_a_previous_failing_pre_push_with_the_same_stdin(tmp_path: Path) -> None:
    repo = _temp_gate_repo(tmp_path)
    log = tmp_path / "hook.log"
    _write_global_hook(
        repo,
        tmp_path,
        "pre-push",
        '#!/usr/bin/env bash\nprintf "args:%s\\n" "$*" >>"$HOOK_LOG"\ncat >>"$HOOK_LOG"\nexit 1\n',
    )
    proc = repo.run(
        (repo.hooks_dir / "pre-push", "origin", "https://example.invalid/repo.git"),
        input=DELETION_ONLY_STDIN,
        env_extra={"HOOK_LOG": str(log)},
    )
    assert proc.returncode == 1, "a failing previous pre-push must block the push"
    logged = log.read_text(encoding="utf-8")
    assert "args:origin https://example.invalid/repo.git" in logged
    assert DELETION_ONLY_STDIN.decode().strip() in logged, "the previous pre-push must see the same stdin the gate saw"


def test_chain_is_a_noop_without_a_previous_hook(tmp_path: Path) -> None:
    repo = _temp_gate_repo(tmp_path)
    (repo.root / ".git" / "hooks" / "commit-msg").write_text("#!/usr/bin/env bash\nexit 9\n", encoding="utf-8")
    (repo.root / ".git" / "hooks" / "commit-msg").chmod(0o755)
    no_pre_push = repo.run((repo.hooks_dir / "pre-push", "origin", "x"))
    assert no_pre_push.returncode == 0, "an absent previous hook is a no-op"
    fallback = repo.run((repo.hooks_dir / "commit-msg", ".git/COMMIT_EDITMSG"))
    assert fallback.returncode == 9, "the chain must fall back to the repo's own .git/hooks"


def test_chain_never_recurses_into_the_enabled_hooks_dir(tmp_path: Path) -> None:
    repo = _temp_gate_repo(tmp_path)
    gitconfig = Path(repo.env["GIT_CONFIG_GLOBAL"])
    gitconfig.write_text(f"[core]\n\thooksPath = {repo.hooks_dir}\n", encoding="utf-8")
    try:
        proc = repo.run((repo.hooks_dir / "commit-msg", "x"))
    except subprocess.TimeoutExpired:  # pragma: no cover - only on a recursion bug
        pytest.fail("the chain recursed into the enabled hooks directory and hung")
    assert proc.returncode == 0, "when the previous path is the enabled dir itself, the chain stops"


def test_the_native_leg_is_time_bounded(tmp_path: Path) -> None:
    """A hung native stage must fail the gate in bounded time, not wedge git push forever.

    The seam is operator-facing, not test-only: JEV_PREPUSH_TIMEOUT sets the per-stage bound
    in seconds for every native stage, and can only make the gate stricter — a smaller bound
    fails more, never less. The default stays the generous finite bound from the ADR."""
    repo = _temp_gate_repo(tmp_path)
    repo.git("commit", "-q", "--allow-empty", "-m", "pushed")
    sha = repo.git("rev-parse", "HEAD")
    zero = "0" * 40

    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    uv_stub = stub_dir / "uv"
    uv_stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    uv_stub.chmod(0o755)
    make_stub = stub_dir / "make"
    make_stub.write_text("#!/usr/bin/env bash\nsleep 30\n", encoding="utf-8")
    make_stub.chmod(0o755)
    leg = repo.root / "scripts" / "ci" / "linux_check.sh"
    leg.write_text("#!/usr/bin/env bash\necho linux-leg-stub\n", encoding="utf-8")

    env = {**repo.env, "PATH": f"{stub_dir}:{repo.env['PATH']}", "JEV_PREPUSH_TIMEOUT": "3", "TMPDIR": str(tmp_path)}
    started = time.monotonic()
    try:
        proc = repo.run(
            ("bash", repo.root / "scripts" / "ci" / "pre_push_check.sh"),
            input=f"refs/heads/push {sha} refs/heads/main {zero}\n".encode(),
            env_extra=env,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("the native stage ran unbounded: a hung make wedged the gate past 20s")
    elapsed = time.monotonic() - started
    assert proc.returncode != 0
    assert b"timed out" in proc.stdout + proc.stderr, "the failure must name the timeout"
    assert elapsed < 15, f"the bound took {elapsed:.1f}s to fire"
    leftovers = [p for p in tmp_path.glob("**/jev-prepush.*") if p.is_dir()]
    assert not leftovers, f"the timed-out check left its clone behind: {leftovers}"


def test_pre_push_gate_checks_the_pushed_commit_in_a_clean_clone(tmp_path: Path) -> None:
    """The native leg's whole behavior, at one boundary: the pushed SHA is checked in a
    detached temporary clone — the stubs run there, not in the pushing tree — and the clone
    is removed after a pass and after a failure."""
    repo = _temp_gate_repo(tmp_path)
    repo.git("commit", "-q", "--allow-empty", "-m", "pushed")
    sha = repo.git("rev-parse", "HEAD")
    zero = "0" * 40

    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    uv_stub = stub_dir / "uv"
    uv_stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    uv_stub.chmod(0o755)
    make_log = tmp_path / "make.log"
    make_stub = stub_dir / "make"
    make_stub.write_text(
        "#!/usr/bin/env bash\n"
        '[ "$1" = -C ] && cd "$2"\n'
        f'printf "cwd:%s head:%s\\n" "$(pwd)" "$(git rev-parse HEAD)" >>"{make_log}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    make_stub.chmod(0o755)
    leg_log = tmp_path / "leg.log"
    (repo.root / "scripts" / "ci" / "linux_check.sh").write_text(
        '#!/usr/bin/env bash\nprintf "leg:%s\\n" "$1" >>"THELOG"\n'.replace("THELOG", str(leg_log)),
        encoding="utf-8",
    )

    env = {**repo.env, "PATH": f"{stub_dir}:{repo.env['PATH']}", "TMPDIR": str(tmp_path)}
    proc = repo.run(
        ("bash", repo.root / "scripts" / "ci" / "pre_push_check.sh"),
        input=f"refs/heads/push {sha} refs/heads/main {zero}\n".encode(),
        env_extra=env,
    )
    assert proc.returncode == 0, proc.stderr.decode()[-600:]
    logged = make_log.read_text(encoding="utf-8")
    clone_dir = logged.split("cwd:")[1].split()[0]
    assert "jev-prepush." in clone_dir, f"make must run inside a temporary clone, saw {clone_dir}"
    assert f"head:{sha}" in logged, "the clone must be detached at exactly the pushed commit"
    assert f"leg:{clone_dir}" in leg_log.read_text(encoding="utf-8"), "the Linux leg must check the same clone"
    assert not [p for p in tmp_path.glob("**/jev-prepush.*") if p.is_dir()], "a passed check must leave no clone"

    # The same push with a failing native stage: still no clone left behind.
    make_stub.write_text("#!/usr/bin/env bash\nexit 3\n", encoding="utf-8")
    make_stub.chmod(0o755)
    failed = repo.run(
        ("bash", repo.root / "scripts" / "ci" / "pre_push_check.sh"),
        input=f"refs/heads/push {sha} refs/heads/main {zero}\n".encode(),
        env_extra=env,
    )
    assert failed.returncode != 0, "a failing native stage must block the push"
    assert b"native:make ci" in failed.stdout + failed.stderr, "the failure must name the check and rerun command"
    assert not [p for p in tmp_path.glob("**/jev-prepush.*") if p.is_dir()], "a failed check must leave no clone"


def test_the_linux_leg_comes_from_the_pushed_commit_when_it_has_one(tmp_path: Path) -> None:
    """GitHub runs the pushed commit's workflow; the gate runs the pushed commit's leg.

    When the pushed commit carries scripts/ci/linux_check.sh, the gate must run that copy —
    its stage list and its Dockerfile, not the pushing checkout's — or a branch that adds a
    CI stage gets its push checked against the wrong list. Commits predating the gate fall
    back to the checkout's copy, and say so."""
    repo = _temp_gate_repo(tmp_path)
    repo.git("commit", "-q", "--allow-empty", "-m", "pushed")
    leg = repo.root / "scripts" / "ci" / "linux_check.sh"
    leg.write_text("#!/usr/bin/env bash\necho pushed-leg-marker\n", encoding="utf-8")
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "carry the leg")
    sha = repo.git("rev-parse", "HEAD")
    zero = "0" * 40

    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    for tool, body in (("uv", "exit 0\n"), ("make", "exit 0\n")):
        stub = stub_dir / tool
        stub.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
        stub.chmod(0o755)
    env = {**repo.env, "PATH": f"{stub_dir}:{repo.env['PATH']}"}

    proc = repo.run(
        ("bash", repo.root / "scripts" / "ci" / "pre_push_check.sh"),
        input=f"refs/heads/push {sha} refs/heads/main {zero}\n".encode(),
        env_extra=env,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined.decode()[-800:]
    assert b"pushed-leg-marker" in combined, "the pushed commit's leg must run, not the checkout's"


def test_the_linux_image_tag_follows_the_dockerfile_content(tmp_path: Path) -> None:
    """A changed Dockerfile must get a different image tag, or a machine that already ran
    the gate keeps checking every later push on the stale image."""
    repo = _temp_gate_repo(tmp_path, with_docker=True)
    log = tmp_path / "docker.log"
    stub_dir = tmp_path / "docker-stub"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "docker"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{log}"\n'
        'case "$1" in\n'
        "  info) exit 0 ;;\n"
        "  image) exit 1 ;;\n"
        "  create) echo fakecid; exit 0 ;;\n"
        "  start) echo staged-ok; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {**repo.env, "PATH": f"{stub_dir}:{repo.env['PATH']}"}

    def build_tag() -> str:
        # Run the temp repo's own copy: its Dockerfile resolution and hash must follow the
        # content in this repo, not the checkout's.
        proc = repo.run(
            ("bash", repo.root / "scripts" / "ci" / "linux_check.sh", str(repo.root), "tag-probe"),
            env_extra=env,
        )
        assert proc.returncode == 0, proc.stdout.decode()[-800:] + proc.stderr.decode()[-800:]
        builds = [line for line in log.read_text(encoding="utf-8").splitlines() if " -t " in line]
        assert builds, f"the leg never built an image: {log.read_text(encoding='utf-8')[-800:]}"
        return builds[-1].split("-t ")[-1].split()[0]

    first = build_tag()
    dockerfile = repo.root / "docker" / "ci-linux.Dockerfile"
    dockerfile.write_text(dockerfile.read_text(encoding="utf-8") + "\n# a content change\n", encoding="utf-8")
    log.write_text("", encoding="utf-8")
    second = build_tag()
    assert first != second, "a changed Dockerfile must produce a different image tag"


def test_the_docker_leg_fails_closed_without_a_daemon(tmp_path: Path) -> None:
    """An unreachable Docker must block the check, never skip the Linux leg.

    Hermetic: a docker stub that answers nothing (exit 1) sits first on PATH, so the test
    does not depend on the host lacking docker — ubuntu-latest HAS a live /usr/bin/docker,
    which a PATH-based dodge would find and happily build an image with."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "docker"
    stub.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "DOCKER_HOST"}
    env["PATH"] = f"{stub_dir}:{env['PATH']}"
    proc = subprocess.run(
        ["bash", str(LINUX_CHECK), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=60,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, "a missing or unreachable docker must block the check"
    assert b"docker is unreachable" in combined, "the block must name docker and the rerun command"
    assert b"make ci-linux" in combined


def test_enabled_hooks_still_reach_previous_hooks_from_an_old_checkout(tmp_path: Path) -> None:
    """No checkout of this repo may run zero hooks (ADR-0056).

    core.hooksPath is per-clone, not per-checkout: an old commit or a linked worktree has no
    .githooks, and a relative hooksPath there means git runs nothing — not even the machine's
    global commit-msg hooks. The enablement must reach the previous hooks from any checkout."""
    repo = _temp_gate_repo(tmp_path)
    repo.git("commit", "-q", "--allow-empty", "-m", "before the gate")
    repo.git("commit", "-q", "--allow-empty", "-m", "gate era")
    log = tmp_path / "hook.log"
    _write_global_hook(
        repo,
        tmp_path,
        "commit-msg",
        '#!/usr/bin/env bash\nprintf "args:%s\\n" "$*" >>"$HOOK_LOG"\nexit 7\n',
    )
    # Land the gate (the enablement runs at this commit), then go back to the bare commit.
    proc = repo.run(("make", "-f", str(REPO / "Makefile"), "hooks"))
    assert proc.returncode == 0, proc.stderr.decode()
    repo.git("checkout", "-q", "HEAD~1")

    hooked = repo.run(
        ("git", "hook", "run", "commit-msg", "--", ".git/COMMIT_EDITMSG"),
        input=b"",
        env_extra={"HOOK_LOG": str(log)},
    )
    assert hooked.returncode == 7, (
        "the previous commit-msg hook must still run from a checkout without the gate: "
        f"rc={hooked.returncode} out={hooked.stdout.decode()[:300]} err={hooked.stderr.decode()[:300]}"
    )
    assert log.exists() and "args:.git/COMMIT_EDITMSG" in log.read_text(encoding="utf-8")


def test_make_hooks_self_check_rejects_an_unreachable_gate(tmp_path: Path) -> None:
    repo = _temp_gate_repo(tmp_path)
    # The installer sets core.hooksPath itself, so the way it can fail is by pointing at a
    # gate that is not there: the checkout's chain removed, no banner.
    (repo.root / "scripts" / "ci" / "hook_chain.sh").unlink()
    proc = repo.run(("make", "-f", str(REPO / "Makefile"), "hooks"))
    assert proc.returncode != 0, "the self-check must fail when core.hooksPath does not reach the gate"
    assert b"does not reach the pre-push gate" in proc.stderr + proc.stdout


def test_make_hooks_self_check_passes_and_enables_the_gate(tmp_path: Path) -> None:
    repo = _temp_gate_repo(tmp_path)
    (repo.root / "scripts" / "ci" / "hook_chain.sh").unlink()  # prove the check is real: restore, then enable
    shutil.copy(HOOK_CHAIN, repo.root / "scripts" / "ci" / "hook_chain.sh")
    proc = repo.run(("make", "-f", str(REPO / "Makefile"), "hooks"))
    assert proc.returncode == 0, proc.stderr.decode()
    configured = repo.run(("git", "config", "core.hooksPath"))
    hooks_path = configured.stdout.decode().strip()
    assert "jev-hooks" in hooks_path, "the enablement must use the common-dir forwarders"
    assert b"pre-push gate reachable" in proc.stdout + proc.stderr, "the gate must report reachability"
