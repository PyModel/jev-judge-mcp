"""Adapter dispatch for the isolated agent run (ADR-0074).

The container adapter's hostile-content proof is the Docker-marked suite. These tests own the
dispatch, the untrusted grade payload, and the infrastructure stop, none of which need a daemon.
"""

import json
import sys
from pathlib import Path

import pytest

from evals.ab import run as ab_run
from evals.ab import tasks
from evals.agent import sandbox_diff
from evals.confinement.launch import ConfinementSpec, parse_grade_payload, read_untrusted_text
from evals.isolation import postprocess

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


def test_an_fsmonitor_payload_runs_when_the_host_diffs(tmp_path: Path) -> None:
    """The payload the confined suite uses is live: the sandbox adapter's host `git add` executes it.
    A confined run of the same payload must not. If this stops firing, the Docker case no longer proves
    the host path is closed."""
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
    sandbox_diff(workdir)
    assert marker.is_file(), "host git add did not execute the planted fsmonitor"


def test_infrastructure_stop_books_the_incurred_cost_and_writes_no_agent_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A grading-container start failure stops the study. The agent's incurred cost is booked once;
    no failed-agent result.json is written."""
    task = tasks.load_task("j10-control-spec")
    setup = ab_run.Setup(
        agent="pi",
        binary=["pi"],
        server_env={},
        base_env={},
        secret="sk-test-not-a-real-key",  # noqa: S106
        python3="python3",
        task_list=(task,),
        confinement=ConfinementSpec(agent_image="jev-eval-missing:no-such", upstreams=(), timeout_s=1),
        reference_ids={task.id: ("tests.not.used",)},
    )

    def stop(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ab_run._InfrastructureStop("grading container did not start", 0.25)  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(ab_run, "run_one", stop)
    reason = ab_run.study(setup, tmp_path)
    assert reason.startswith("stopped: isolation infrastructure:")
    booked = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))["runs"]
    assert len(booked) == 1 and set(booked.values()) == {0.25}
    assert next(iter(booked)).startswith(f"{task.id}.")
    assert list(tmp_path.glob("*/result.json")) == []


def test_an_infrastructure_error_escapes_write_record_without_a_failed_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production grades inside `write_record`. An infrastructure error must not become the
    failed-agent row that `write_record` writes for every other exception."""
    from evals.ab import ledger
    from evals.ab.stream import Trace
    from evals.agent import AgentRunResult
    from evals.isolation import IsolationInfrastructureError
    from evals.spend import SpendLedger

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
    with pytest.raises(ab_run._InfrastructureStop) as stopped:  # pyright: ignore[reportPrivateUsage]
        ab_run._record_inside(  # pyright: ignore[reportPrivateUsage]
            tmp_path, run, "t.A.r1", task, "A", 1, setup, book, tmp_path / "missing.jsonl"
        )
    assert stopped.value.incurred_usd == 0.25
    assert not (tmp_path / "result.json").exists()
