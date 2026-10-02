"""The multi-file diff path jev_review and jev_gate share (ADR-0066): the `[{path, patch}]` shape, its
two budgets, one review request per fitting file, and the frame both tools publish over the result.

The two tools differ only in how they refuse a budget (jev_review raises it typed, jev_gate returns
the isError payload), in the purpose text each sends, and in what jev_gate adds after the files (one
verification ask); everything the per-file pass knows lives here once.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from jev_judge_mcp.domain import Usage
from jev_judge_mcp.limits import GATE, GateCaps
from jev_judge_mcp.policy import Action
from jev_judge_mcp.providers import Evaluation
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import Runtime, ToolError
from jev_judge_mcp.tools.observed import worst_action
from jev_judge_mcp.tools.review_half import ReviewHalf, ReviewSettings, project_review, review_docs, review_questions
from jev_judge_mcp.validation.caps import CapLedger, CapScope, exceeds, gate_diff_aggregate_error, gate_diff_files_error


def file_patches(diff: object) -> list[dict[str, str]]:
    """A file-list `diff` as sent: one `{path, patch}` record per item, or a `ToolError`."""
    if not isinstance(diff, list):
        raise ToolError("diff file list was not a list", code="invalid_arguments")
    files: list[dict[str, str]] = []
    for raw in cast(list[object], diff):
        if not isinstance(raw, dict):
            raise ToolError("diff file list item was not an object", code="invalid_arguments")
        record = cast(dict[str, object], raw)
        files.append({"path": str(record["path"]), "patch": str(record["patch"])})
    return files


def file_list_refusal(files: Sequence[Mapping[str, str]], caps: GateCaps = GATE) -> str | None:
    """The budget text a file list is refused with, or `None` (ADR-0066 and its amendment).

    The joined patches first, then the count: one review request runs per file, so the count is a
    budget too. The caller gives the text its tool's shape — jev_review raises it typed
    `input_too_large`, jev_gate returns the isError payload — so the sentence has one owner.
    """
    if exceeds(sum(length(item["patch"]) for item in files), caps.aggregate_evidence_units):
        return gate_diff_aggregate_error(caps.aggregate_evidence_units)
    if exceeds(len(files), caps.files_max):
        return gate_diff_files_error(caps.files_max)
    return None


def file_actions(reviewed: Iterable[tuple[str, Action]]) -> dict[str, Action]:
    """Each reviewed file path to that file's review action (ADR-0066 amendment).

    The payload's review half shows one file's scores while `action` is the worst of them;
    this mapping names the file that drove it. A repeated path keeps its worst action, so the
    mapping can never soften the headline. Unreviewed files do not appear here; they stay in
    `unreviewed_files`.
    """
    actions: dict[str, Action] = {}
    for path, action in reviewed:
        current = actions.get(path)
        actions[path] = action if current is None else worst_action([current, action])
    return actions


@dataclass(frozen=True, slots=True)
class FileListReview:
    """What one pass over a file list produced, before either tool frames it (ADR-0066)."""

    halves: list[ReviewHalf]
    evaluations: list[Evaluation]
    reviewed_paths: list[str]
    unreviewed: list[str]
    fitting: list[dict[str, str]]
    """The files that were reviewed, as sent: jev_gate's verification ask cites each one."""
    unhashed_tests: bool
    truncated: bool
    """Whether any file's documents were cut; an unreviewed file is `partial`, never a cut."""
    scopes: frozenset[CapScope]

    def review_half(self, action: Action) -> dict[str, object]:
        """The review half both tools publish: the first reviewed file's half under `action`, then
        `score_file`, `reviewed_files`, and `file_actions` (ADR-0066 amendment). `tests_weight` is
        the caller's to add, where its payload places it."""
        payload = dict(self.halves[0].payload)
        payload["action"] = action
        payload["score_file"] = self.reviewed_paths[0]
        payload["reviewed_files"] = self.reviewed_paths
        payload["file_actions"] = file_actions(
            zip(self.reviewed_paths, [half.action for half in self.halves], strict=True)
        )
        return payload


async def review_file_list(
    files: Sequence[Mapping[str, str]],
    args: dict[str, Any],
    runtime: Runtime,
    settings: ReviewSettings,
    *,
    purpose: str,
    doc_units: int,
) -> FileListReview:
    """One review request per file under `doc_units`; a file over it is unreviewed (ADR-0066).

    Each fitting file is asked the plain rubric over its own documents through its own ledger, so a
    cut in one file is recorded without joining the files back into a string that would be cut.
    `purpose` is the asking tool's own framing of the state.
    """
    fitting = [dict(item) for item in files if length(item["patch"]) <= doc_units]
    unreviewed = [item["path"] for item in files if length(item["patch"]) > doc_units]
    halves: list[ReviewHalf] = []
    evaluations: list[Evaluation] = []
    reviewed_paths: list[str] = []
    unhashed_tests = False
    truncated = False
    scopes: frozenset[CapScope] = frozenset()
    for item in fitting:
        ledger = CapLedger()
        docs = review_docs({**args, "diff": item["patch"]}, ledger, doc_units)
        evaluation = await runtime.ask(
            {"purpose": purpose, "request": docs.request, "diff": docs.diff, "tests": docs.tests},
            review_questions(),
        )
        halves.append(project_review(evaluation.answers, settings, ledger.context_cut))
        evaluations.append(evaluation)
        reviewed_paths.append(item["path"])
        if docs.tests and not args.get("tests_sha256"):
            unhashed_tests = True
        truncated = truncated or ledger.context_cut
        scopes |= ledger.scopes
    return FileListReview(halves, evaluations, reviewed_paths, unreviewed, fitting, unhashed_tests, truncated, scopes)


def combined(evaluations: list[Evaluation]) -> Evaluation:
    """One frame for per-file asks: usage summed across every call, request id kept when one sent it."""
    first = evaluations[0]
    request_id = next((item.request_id for item in evaluations if item.request_id), None)
    return Evaluation(
        {},
        Usage(
            sum(item.usage.input_tokens for item in evaluations),
            sum(item.usage.output_tokens for item in evaluations),
        ),
        first.provider,
        first.model,
        request_id=request_id,
    )
