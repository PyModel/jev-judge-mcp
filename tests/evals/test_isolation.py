"""Adapter dispatch for the isolated agent run (ADR-0074).

The container adapter's hostile-content proof is the Docker-marked suite. These tests own the
dispatch, the untrusted grade payload, and the infrastructure stop, none of which need a daemon.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from evals.ab import ledger, tasks
from evals.ab import run as ab_run
from evals.agent import sandbox_diff
from evals.confinement import launch
from evals.confinement.launch import ConfinementSpec, parse_grade_payload, read_untrusted_text
from evals.isolation import IsolationInfrastructureError, IsolationResult, postprocess
from evals.spend import SpendLedger
from evals.study_runner import InfrastructureStop
from tests.evals.booking_cases import write_result

_GRADE = {
    "acceptance_passed": 1,
    "acceptance_total": 1,
    "original_passed": 2,
    "original_total": 2,
    "regressions": [],
    "protected_changed": [],
    "preexisting_altered": [],
    "added_tests": [{"file": "tests/test_added.py", "name": "test_added", "outcome": "pass", "relevant": True}],
}


def test_a_grade_payload_is_accepted_only_when_it_matches_the_schema() -> None:
    """The container's grade.json is untrusted. A drifted key, a bool count, or a bad outcome is
    refused — accepting any of those would record attacker-shaped grade fields."""
    parsed = parse_grade_payload(_GRADE)
    assert parsed.acceptance_passed == 1 and parsed.added_tests[0].relevant is True
    for payload, match in (
        ({**_GRADE, "extra": 1}, "extra"),
        ({key: value for key, value in _GRADE.items() if key != "regressions"}, "missing"),
        ({**_GRADE, "acceptance_passed": True}, "non-negative int"),
        (
            {**_GRADE, "added_tests": [{"file": "t.py", "name": "test_x", "outcome": "nope", "relevant": True}]},
            "bad field",
        ),
    ):
        with pytest.raises(ValueError, match=match):
            parse_grade_payload(payload)


def test_untrusted_output_refuses_a_symlink(tmp_path: Path) -> None:
    """A grading container that plants a symlink in its output dir must not make the host open the target."""
    secret = tmp_path / "secret.txt"
    secret.write_text("host-only-secret", encoding="utf-8")
    link = tmp_path / "grade.json"
    link.symlink_to(secret)
    with pytest.raises(ValueError, match="not a regular file"):
        read_untrusted_text(link)


def test_the_sandbox_adapter_grades_on_the_host(tmp_path: Path) -> None:
    """No confinement selects the sandbox adapter. It grades the tree on the host and returns a grade."""
    task = tasks.load_task("j10-control-spec")
    workdir = tmp_path / "tree"
    tasks.materialize(task, workdir)
    graded = postprocess(
        workdir=workdir,
        task=task,
        python=sys.executable,
        confinement=None,
        reference_ids={},
    )
    assert graded.grade.acceptance_total > 0
    assert graded.grade.original_total > 0
    assert isinstance(graded.patch, str)


def test_a_confined_run_without_reference_ids_is_refused_before_docker(tmp_path: Path) -> None:
    """The container adapter is selected when a spec is set, and it will not grade without the
    study-start ids. Falling through to the host grader would not raise this."""
    task = tasks.load_task("j10-control-spec")
    workdir = tmp_path / "tree"
    workdir.mkdir()
    with pytest.raises(ValueError, match="reference ids"):
        postprocess(
            workdir=workdir,
            task=task,
            python=sys.executable,
            confinement=ConfinementSpec(agent_image="jev-eval-missing:no-such", upstreams=(), timeout_s=1),
            reference_ids={},
        )


def test_host_diff_disables_the_worktrees_fsmonitor(tmp_path: Path) -> None:
    task = tasks.load_task("j10-control-spec")
    workdir = tmp_path / "tree"
    tasks.materialize(task, workdir)
    marker = tmp_path / "marker"
    script = tmp_path / "fs.sh"
    script.write_text(f"#!/bin/sh\necho ran >> {marker}\n", encoding="utf-8")
    script.chmod(0o755)
    subprocess_config = workdir / ".git" / "config"
    subprocess_config.write_text(
        subprocess_config.read_text(encoding="utf-8") + f"\n[core]\n\tfsmonitor = {script}\n",
        encoding="utf-8",
    )
    (workdir / "new.txt").write_text("a visible edit\n")
    assert "+a visible edit" in sandbox_diff(workdir)
    assert not marker.exists(), "host git add executed the worktree's fsmonitor"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("before-launch", None),
        ("remove-agent", ledger.POLICIES["claude"].run_bound_usd),
        ("teardown", 1.7),
        ("clear-root", 1.7),
        ("interrupt", None),
        ("grading", 0.25),
        ("hard-kill", None),
    ],
)
def test_confined_study_accounts_for_a_stop_at_the_launch_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    failure: str,
    expected: float | None,
) -> None:
    class AbruptExit(BaseException):
        pass

    task = tasks.load_task("j10-control-spec")
    setup = ab_run.Setup(
        agent="claude",
        binary=["unused"],
        server_env={},
        base_env={},
        secret="sk-test-not-a-real-key",  # noqa: S106
        python3="python3",
        task_list=(task,),
        confinement=ConfinementSpec(agent_image="jev-eval-missing:no-such", upstreams=(), timeout_s=1),
        reference_ids={task.id: ("tests.not.used",)},
    )

    first = ab_run.schedule([task], 1)[0][2][0]
    rid = ab_run.run_id(task.id, first, 1)
    launched = 0
    clear = launch.clear_leased_directory

    def broker(_name: str) -> None:
        if failure == "before-launch":
            raise launch.ConfinementError("broker did not start")

    def capture(*_args: object, **_kwargs: object) -> tuple[str, str, int]:
        nonlocal launched
        launched += 1
        if failure == "hard-kill":
            assert launched == 1, "resume relaunched the interrupted agent"
            raise AbruptExit
        return '{"type":"result","total_cost_usd":1.7}\n', "", 0

    def grade(**_kwargs: object) -> IsolationResult:
        if failure == "remove-agent":
            pytest.fail("grading began before the agent stopped")
        if failure == "interrupt":
            raise KeyboardInterrupt
        if failure == "grading":
            raise IsolationInfrastructureError("grading container did not start", incurred_usd=0.25)
        return IsolationResult("", parse_grade_payload(_GRADE))

    def docker(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:3] == ["docker", "rm", "-f"] and (
            (failure == "remove-agent" and argv[-1].startswith("jev-eval-agent-"))
            or (failure in ("teardown", "interrupt", "grading") and argv[-1].startswith("jev-eval-broker-"))
        ):
            return subprocess.CompletedProcess(argv, 1, "", "unable to remove")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def clear_root(root: Path, *, keep_lease: bool = False) -> None:
        clear(root, keep_lease=keep_lease)
        if failure == "clear-root":
            raise PermissionError("could not finish clearing the run root")

    monkeypatch.setattr(launch, "docker_run", docker)
    monkeypatch.setattr(launch, "wait_for_broker", broker)
    monkeypatch.setattr(launch, "capture_output", capture)
    monkeypatch.setattr(launch, "clear_leased_directory", clear_root)
    monkeypatch.setattr(ab_run, "postprocess", grade)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    request.addfinalizer(launch.reap)
    other = ab_run.run_id(task.id, "A" if first == "B" else "B", 1)
    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            ab_run.study(setup, tmp_path, repeats=1)
        expected = ledger.POLICIES["claude"].run_bound_usd
    elif failure == "hard-kill":
        with pytest.raises(AbruptExit):
            ab_run.study(setup, tmp_path, repeats=1)
        assert not (tmp_path / "ledger.json").exists()
        assert not list(tmp_path.glob("*/result.json"))
        write_result(tmp_path, other, 0.0, bench_keys=False)
        assert rid in ab_run.study(setup, tmp_path, repeats=1)
        expected = ledger.POLICIES["claude"].run_bound_usd
    else:
        assert ab_run.study(setup, tmp_path, repeats=1).startswith("stopped: isolation infrastructure:")
    booked = dict(SpendLedger.load(tmp_path / "ledger.json", ledger.POLICIES["claude"]).runs)
    if failure == "hard-kill":
        assert booked.pop(other) == 0.0
    assert booked == ({} if expected is None else {rid: expected})
    assert (tmp_path / rid / "result.json").exists() == (failure in ("teardown", "clear-root"))
    assert launched == (0 if failure == "before-launch" else 1)


def test_an_infrastructure_error_escapes_write_record_without_a_failed_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production grades inside `write_record`. An infrastructure error must not become the
    failed-agent row that `write_record` writes for every other exception."""
    from evals.ab import ledger
    from evals.ab.stream import Trace
    from evals.agent import AgentRunResult
    from evals.isolation import IsolationInfrastructureError

    task = tasks.load_task("j10-control-spec")
    workdir = tmp_path / "tree"
    workdir.mkdir()
    setup = ab_run.Setup(
        agent="pi",
        binary=["pi"],
        server_env={},
        base_env={},
        secret="sk-test-not-a-real-key",  # noqa: S106
        python3=sys.executable,
        confinement=ConfinementSpec(agent_image="jev-eval-missing:no-such", upstreams=(), timeout_s=1),
        reference_ids={task.id: ("id",)},
    )
    book = SpendLedger.load(tmp_path / "ledger.json", ledger.POLICIES["pi"])
    run = AgentRunResult(
        argv=(),
        workdir=workdir,
        stdout="",
        stderr="",
        returncode=0,
        wall_s=1.0,
        trace=Trace(result={"total_cost_usd": 0.25}),
        status="ok",
    )

    def boom(**_kwargs: object) -> None:
        raise IsolationInfrastructureError("docker is unavailable")

    monkeypatch.setattr(ab_run, "postprocess", boom)
    with pytest.raises(InfrastructureStop) as stopped:
        ab_run._record_inside(  # pyright: ignore[reportPrivateUsage]
            tmp_path, run, "t.A.r1", task, "A", 1, setup, book, tmp_path / "missing.jsonl"
        )
    assert stopped.value.incurred_usd == 0.25
    assert not (tmp_path / "result.json").exists()
