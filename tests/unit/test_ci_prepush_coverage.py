"""The pre-push gate cannot drift from the workflow it mirrors (ADR-0056).

The tracked .githooks/pre-push runs every command the GitHub `ci` workflow runs — natively
(`make ci`) and on Linux (scripts/ci/linux_check.sh, in the digest-pinned image from
docker/ci-linux.Dockerfile) — against a clean temporary clone of each pushed commit. This
test reads the same files the gate reads and fails whenever ci.yml gains a command, an
action, or a version pin the gate does not emulate, so a stage can only enter CI by teaching
the gate in the same commit. Unknown shapes fail closed: an unparseable workflow cannot
quietly widen what a push may skip.
"""

import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
MAKEFILE = REPO / "Makefile"
HOOK = REPO / ".githooks" / "pre-push"
LINUX_CHECK = REPO / "scripts" / "ci" / "linux_check.sh"
PRE_PUSH_CHECK = REPO / "scripts" / "ci" / "pre_push_check.sh"
HOOK_CHAIN = REPO / "scripts" / "ci" / "hook_chain.sh"
DOCKERFILE = REPO / "docker" / "ci-linux.Dockerfile"

# Every client-side hook name in githooks(5): after `make hooks`, git runs only .githooks for
# this clone, so each of these must be offered here and chained to the hooks path that was
# in effect before, or enabling the gate would silently drop the machine's other hooks
# (the global commit-msg strippers, for one).
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

# The `uses:` steps the gate emulates: checkout (the temporary clone), setup-uv (uv baked
# into the image), setup-node (Node 24.19.0 baked in, asserted per check), setup-python 3.10
# (the smoke job's old-Python entry guard), and upload-artifact (the build stage produces
# dist/ in the container; only the upload itself is CI-side plumbing).
ALLOWED_USES = {
    "actions/checkout@v4",
    "astral-sh/setup-uv@v6",
    "actions/setup-node@v4",
    "actions/setup-python@v5",
    "actions/upload-artifact@v4",
}
ALLOWED_RUN = {
    "uv sync --locked --all-extras",
    "python scripts/ci_old_python_entry.py",
}
PAID_TARGETS = ("security-live", "eval-live", "ab", "load")
PAID_FLAGS = ("JEV_EVAL_LIVE", "JEV_AB_LIVE")


@dataclass
class Step:
    """One workflow step: an `action ref, a `run` command, and any `with:` values."""

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

    def unknown(lineno: int, line: str, reason: str) -> None:
        pytest.fail(f"ci.yml:{lineno}: unrecognized {reason}: {line!r}")

    for lineno, raw in enumerate(WORKFLOW.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()

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
            if item.startswith("run:"):
                steps.append(Step(run=item[len("run:") :].strip()))
                in_with = False
                continue
            unknown(lineno, line, "step")
        if indent == 8 and content == "with:":
            in_with = True
            continue
        if in_with and indent == 10:
            key, sep, value = content.partition(":")
            if not sep or not steps:
                unknown(lineno, line, "with key")
            steps[-1].with_values[key.strip()] = value.strip()
            continue
        unknown(lineno, line, "line")
    return steps


def _ci_make_targets(steps: list[Step]) -> set[str]:
    """The make targets ci.yml runs; anything but `make <targets>` is classified elsewhere."""
    targets: set[str] = set()
    for step in steps:
        if step.run is None:
            continue
        if step.run in ALLOWED_RUN:
            continue
        if not step.run.startswith("make "):
            pytest.fail(f"ci.yml runs a command the gate cannot classify: {step.run!r}")
        targets.update(step.run[len("make ") :].split())
    return targets


def _linux_stage_targets() -> set[str]:
    """The make targets scripts/ci/linux_check.sh runs, from its own run lines."""
    return set(re.findall(r"^run make:(\S+) ", LINUX_CHECK.read_text(encoding="utf-8"), re.MULTILINE))


def _makefile_ci_targets() -> set[str]:
    line = next(line for line in MAKEFILE.read_text(encoding="utf-8").splitlines() if line.startswith("ci:"))
    return set(line[len("ci:") :].split())


def test_every_workflow_step_is_emulated() -> None:
    steps = _parse_steps()
    assert steps, "no steps parsed from ci.yml; the parser's shape assumptions broke"
    for step in steps:
        if step.action is not None:
            assert step.action in ALLOWED_USES, f"ci.yml runs {step.action!r}, which the gate cannot emulate"
        node = step.with_values.get("node-version")
        if node is not None:
            assert node == '"24.19.0"', f"parity Node drifted from the oracle's 24.19.0: {node}"
        python = step.with_values.get("python-version")
        if python is not None:
            assert python == '"3.10"', f"the old-Python entry guard is defined against 3.10, not {python}"


def test_every_workflow_command_runs_in_a_gate_leg() -> None:
    steps = _parse_steps()
    ci_targets = _ci_make_targets(steps)
    assert ci_targets == _linux_stage_targets(), (
        "ci.yml's make targets and the gate's Linux stages differ; teach scripts/ci/linux_check.sh in the same commit"
    )
    assert ci_targets <= _makefile_ci_targets(), (
        "ci.yml runs a make target outside `make ci`, so the native leg misses it"
    )
    for step in steps:
        if step.run is not None and step.run not in ALLOWED_RUN and not step.run.startswith("make "):
            pytest.fail(f"ci.yml runs {step.run!r}, which the gate does not run")


def test_native_leg_runs_sync_and_make_ci() -> None:
    text = PRE_PUSH_CHECK.read_text(encoding="utf-8")
    assert "uv sync --locked --all-extras" in text, "the native leg must sync, as every ci.yml job does"
    assert re.search(r"make -C \"\$TMP_DIR/src\" ci\b", text), (
        "the native leg must run `make ci` from the pushed commit's Makefile"
    )
    assert "linux_check.sh" in text, "the native leg alone is not the gate: the Linux leg must also run"
    assert "checkout --quiet --detach" in text, "the check must run a detached clean clone of the pushed commit"


def test_linux_leg_pins_node_and_the_old_python_entry() -> None:
    text = LINUX_CHECK.read_text(encoding="utf-8")
    assert "v24.19.0" in text, "the container's Node must be asserted equal to the workflow's pin"
    assert "scripts/ci_old_python_entry.py" in text, "the smoke job's old-Python entry step must run"
    assert "uv python install 3.10" in text, "the entry guard needs Python 3.10 first on PATH"
    assert "CI=true" in text, "the Linux leg must run with CI=true, as GitHub does"


def test_every_linux_stage_is_time_bounded() -> None:
    for line in LINUX_CHECK.read_text(encoding="utf-8").splitlines():
        if re.match(r"^run \S+ ", line):
            assert re.match(r"^run \S+ \d+ ", line), f"stage without a finite timeout: {line!r}"


def test_hook_and_targets_exist_and_wiring_matches() -> None:
    assert HOOK.exists() and os.access(HOOK, os.X_OK), ".githooks/pre-push must exist and be executable"
    assert "hook_chain.sh" in HOOK.read_text(encoding="utf-8"), "the hook must forward through the chain"
    assert "pre_push_check.sh" in HOOK_CHAIN.read_text(encoding="utf-8"), (
        "the chain must run the gate before any previous pre-push"
    )
    makefile = MAKEFILE.read_text(encoding="utf-8")
    hooks_block = re.search(r"^hooks:\n((?:\t[^\t].*\n|\t\t.*\n)+)", makefile, re.MULTILINE)
    assert hooks_block is not None, "make hooks must exist"
    assert "git config core.hooksPath .githooks" in hooks_block.group(1)
    assert "git hook run pre-push" in hooks_block.group(1), (
        "make hooks must prove the gate is reachable; a silent enable can hide a broken install"
    )
    assert "</dev/null" in hooks_block.group(1), "the self-check must run with empty stdin"
    ci_linux_recipe = re.search(r"^ci-linux:\n\t(\S.*)$", makefile, re.MULTILINE)
    assert ci_linux_recipe is not None and "linux_check.sh" in ci_linux_recipe.group(1)


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
    for script in (LINUX_CHECK, PRE_PUSH_CHECK, HOOK):
        text = script.read_text(encoding="utf-8")
        for target in PAID_TARGETS:
            assert not re.search(rf"^run .*make.*\b{target}\b", text, re.MULTILINE), (
                f"{script.name} must not run the paid {target} stage"
            )
        for flag in PAID_FLAGS:
            assert flag not in text, f"{script.name} must not set {flag}"
    for stage in _linux_stage_targets():
        assert stage not in PAID_TARGETS


def test_both_legs_clean_up_after_themselves() -> None:
    pre = PRE_PUSH_CHECK.read_text(encoding="utf-8")
    assert "rm -rf" in pre and "trap cleanup EXIT" in pre
    for signal, code in (("INT", 130), ("TERM", 143), ("HUP", 129)):
        assert f"trap 'exit {code}' {signal}" in pre, f"the hook must clean up on SIG{signal}"
    linux = LINUX_CHECK.read_text(encoding="utf-8")
    assert "docker rm -f" in linux and "trap cleanup EXIT" in linux
    for signal, code in (("INT", 130), ("TERM", 143), ("HUP", 129)):
        assert f"trap 'exit {code}' {signal}" in linux, f"the container must be removed on SIG{signal}"
    chain = HOOK_CHAIN.read_text(encoding="utf-8")
    assert "trap cleanup EXIT" in chain
    for signal, code in (("INT", 130), ("TERM", 143), ("HUP", 129)):
        assert f"trap 'exit {code}' {signal}" in chain, f"the chain must clean up on SIG{signal}"


def test_the_docker_leg_fails_closed_without_a_daemon() -> None:
    text = LINUX_CHECK.read_text(encoding="utf-8")
    assert "docker info" in text, "an unreachable Docker must block the check, never skip the Linux leg"


def test_linux_leg_matches_the_runner_environment() -> None:
    """Firstmate's replica evidence: missing ps fails the smoke census, root fails the
    security wire tests, and a project-scoped 3.10 find resolves 3.12 (ADR-0056)."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    for tool in ("git", "make", "procps"):
        assert tool in dockerfile, f"the CI image must provide {tool}, as ubuntu-latest does"
    assert "SHASUMS256" in dockerfile, "the Node tarball must be verified against the release checksums"
    assert "useradd -m -u 1000" in dockerfile, "the image must ship a non-root runner, like GitHub's runner"
    linux = LINUX_CHECK.read_text(encoding="utf-8")
    assert "runuser -u runner" in linux, "the stages must run as a non-root user, like GitHub's runner"
    preflight = re.search(r"^for tool in (.+?); do$", linux, re.MULTILINE)
    assert preflight, "the runner must preflight the system tools the tests shell out to"
    for tool in ("uv", "uvx", "git", "make", "ps", "node", "python3"):
        assert tool in preflight.group(1).split(), f"the preflight must fail the gate when {tool} is missing"
    assert "uv python find --no-project 3.10" in linux, (
        "inside the project, a project-scoped find resolves 3.12; the entry guard needs 3.10"
    )
    assert "/home/runner/.cache/uv" in linux, "caches live in the runner user's home"
    assert "--init" in linux, (
        "the container must reap orphans like the runner does; without init, killed"
        " process groups linger as zombies and the eval group-death assertions see them alive"
    )


# --- the hook chain (ADR-0056): enabling .githooks must not orphan the previous hooks ---


def test_every_githooks_client_hook_is_offered() -> None:
    offered = {p.name for p in HOOK.parent.iterdir() if p.is_file() and not p.name.startswith(".")}
    assert offered == set(CLIENT_HOOKS), "the .githooks forwarder set must match githooks(5)'s client hooks exactly"
    for name in CLIENT_HOOKS:
        shim = HOOK.parent / name
        assert os.access(shim, os.X_OK), f"{name} must be executable"
        text = shim.read_text(encoding="utf-8")
        assert "hook_chain.sh" in text, f"{name} must chain through hook_chain.sh"
        assert f'" {name} ' in text or f'" {name}"' in text, f"{name} must pass its own name to the chain"


@dataclass
class TempRepo:
    root: Path
    env: dict[str, str]

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


def _temp_gate_repo(tmp_path: Path) -> TempRepo:
    """A throwaway clone of the gate's files: real .githooks and scripts, isolated config.

    Every git call sees GIT_CONFIG_GLOBAL pointed at an empty temp file and
    GIT_CONFIG_NOSYSTEM set, so the machine's real global and system config never decides
    a test's outcome — and is never written to.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    shutil.copytree(REPO / ".githooks", repo / ".githooks")
    shutil.copytree(REPO / "scripts" / "ci", repo / "scripts" / "ci")
    gitconfig = tmp_path / "global.gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        HOME=str(home),
        GIT_CONFIG_GLOBAL=str(gitconfig),
        GIT_CONFIG_NOSYSTEM="1",
    )
    subprocess.run(("git", "init", "-q", str(repo)), capture_output=True, check=True, env=env)
    return TempRepo(root=repo, env=env)


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


def test_chain_runs_a_previous_commit_msg_hook_with_args_and_stdin(tmp_path: Path) -> None:
    repo = _temp_gate_repo(tmp_path)
    log = tmp_path / "hook.log"
    hook_body = (
        "#!/usr/bin/env bash\n"
        'printf "args:%s\\n" "$*" >>"$HOOK_LOG"\n'
        'printf "stdin:%s\\n" "$(cat)" >>"$HOOK_LOG"\n'
        'exit "${HOOK_EXIT:-0}"\n'
    )
    _write_global_hook(repo, tmp_path, "commit-msg", hook_body)
    proc = repo.run(
        (repo.root / ".githooks" / "commit-msg", ".git/COMMIT_EDITMSG"),
        input=b"a commit message\n",
        env_extra={"HOOK_LOG": str(log), "HOOK_EXIT": "7"},
    )
    assert proc.returncode == 7, "the previous hook's failure exit must propagate"
    logged = log.read_text(encoding="utf-8")
    assert "args:.git/COMMIT_EDITMSG" in logged, "the previous hook must receive the same args"
    assert "stdin:a commit message" in logged, "the previous hook must receive stdin"


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
        (repo.root / ".githooks" / "pre-push", "origin", "https://example.invalid/repo.git"),
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
    no_pre_push = repo.run((repo.root / ".githooks" / "pre-push", "origin", "x"))
    assert no_pre_push.returncode == 0, "an absent previous hook is a no-op"
    fallback = repo.run((repo.root / ".githooks" / "commit-msg", ".git/COMMIT_EDITMSG"))
    assert fallback.returncode == 9, "the chain must fall back to the repo's own .git/hooks"


def test_chain_never_recurses_into_githooks(tmp_path: Path) -> None:
    repo = _temp_gate_repo(tmp_path)
    gitconfig = Path(repo.env["GIT_CONFIG_GLOBAL"])
    gitconfig.write_text(f"[core]\n\thooksPath = {repo.root / '.githooks'}\n", encoding="utf-8")
    try:
        proc = repo.run((repo.root / ".githooks" / "commit-msg", "x"))
    except subprocess.TimeoutExpired:  # pragma: no cover - only on a recursion bug
        pytest.fail("the chain recursed into .githooks and hung")
    assert proc.returncode == 0, "when the previous path is .githooks itself, the chain stops"


def test_make_hooks_self_check_rejects_an_unreachable_gate(tmp_path: Path) -> None:
    repo = _temp_gate_repo(tmp_path)
    # The recipe sets core.hooksPath itself, so the way it can fail is by pointing at a
    # gate that is not there: no .githooks, no banner.
    shutil.rmtree(repo.root / ".githooks")
    proc = repo.run(("make", "-f", str(REPO / "Makefile"), "hooks"))
    assert proc.returncode != 0, "the self-check must fail when core.hooksPath does not reach the gate"
    assert b"does not reach the pre-push gate" in proc.stderr + proc.stdout


def test_make_hooks_self_check_passes_and_enables_the_gate(tmp_path: Path) -> None:
    repo = _temp_gate_repo(tmp_path)
    proc = repo.run(("make", "-f", str(REPO / "Makefile"), "hooks"))
    assert proc.returncode == 0, proc.stderr.decode()
    configured = repo.run(("git", "config", "core.hooksPath"))
    assert configured.stdout.decode().strip() == ".githooks"
    assert b"pre-push gate reachable" in proc.stdout + proc.stderr, "the gate must report reachability"
