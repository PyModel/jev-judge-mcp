"""Which isolation adapter grades a run, and the result both adapters return.

Container mechanics stay in `evals.confinement.launch`. Dry-run sandbox mechanics stay in
`evals.agent`. This module only selects the adapter and names the shared result (ADR-0074).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from evals.ab.grade import Grade, grade
from evals.ab.tasks import Task
from evals.agent import sandbox_diff
from evals.confinement.launch import ConfinementSpec, ContainerStartError, confined_postprocess


@dataclass(frozen=True)
class IsolationResult:
    """What crosses back from an adapter: the diff text and the grade. Nothing else."""

    patch: str
    grade: Grade


class IsolationInfrastructureError(Exception):
    """Docker is unavailable, or the grading container cannot start.

    Not an agent failure. `incurred_usd` is the cost the agent already incurred, or None when the
    agent never ran. The study books that cost and stops; it does not write a failed-agent result.
    """

    def __init__(self, message: str, *, incurred_usd: float | None = None) -> None:
        super().__init__(message)
        self.incurred_usd = incurred_usd


def postprocess(
    *,
    workdir: Path,
    task: Task,
    python: str,
    confinement: ConfinementSpec | None,
    reference_ids: Mapping[str, tuple[str, ...]],
) -> IsolationResult:
    """Grade `workdir` with the adapter the run selected.

    A confinement spec selects the container adapter. None selects the macOS sandbox adapter, which
    grades on the host. The container adapter does not open `workdir` on the host.
    """
    if confinement is None:
        return IsolationResult(sandbox_diff(workdir), grade(workdir, task, python))
    ids = reference_ids.get(task.id)
    if not ids:
        raise ValueError(f"confined run has no study-start reference ids for {task.id}")
    try:
        patch, result = confined_postprocess(confinement.agent_image, workdir, task.id, ids)
    except ContainerStartError as error:
        raise IsolationInfrastructureError(str(error)) from error
    return IsolationResult(patch, result)
