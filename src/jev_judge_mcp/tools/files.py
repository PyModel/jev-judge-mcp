"""The multi-file diff path jev_review and jev_gate share (ADR-0066): the `[{path, patch}]` shape, its
two budgets, one review request per fitting file, and the frame both tools publish over the result.

The two tools differ only in how they refuse a budget (jev_review raises it typed, jev_gate returns
the isError payload), in the purpose text each sends, and in what jev_gate adds after the files (one
verification ask); everything the per-file pass knows lives here once.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import anyio

from jev_judge_mcp.domain import Question, Usage
from jev_judge_mcp.gate_budget import GateBudgetError, check_gate_budget
from jev_judge_mcp.gate_diagnostics import Blocker, review_blockers, size_blocker
from jev_judge_mcp.limits import GATE, GateCaps
from jev_judge_mcp.policy import Action
from jev_judge_mcp.providers import Evaluation
from jev_judge_mcp.serialize import stringify_compact
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import Runtime, ToolError
from jev_judge_mcp.tools.gate_evidence import Citations, add_citations, grounded_review
from jev_judge_mcp.tools.observed import worst_action
from jev_judge_mcp.tools.review_half import (
    ReviewDocs,
    ReviewHalf,
    ReviewSettings,
    project_review,
    review_docs,
    review_questions,
)
from jev_judge_mcp.validation.caps import CapLedger, CapScope, exceeds, gate_diff_aggregate_error, gate_diff_files_error

REVIEW_FANOUT = 8
"""Per-file reviews one file list holds open at once. The sequential pass this replaced held one; a
64-file list at once would be a paid burst the provider's rate limit was never measured against.
The ADR-0069 process cap (`JEV_MCP_MAX_INFLIGHT`) still applies on top (ADR-0066 amendment)."""


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
    unhashed_tests: bool
    truncated: bool
    """Whether any file's documents were cut; an unreviewed file is `partial`, never a cut."""
    scopes: frozenset[CapScope]

    blockers: list[Blocker]
    context_omissions: list[dict[str, object]]

    def review_half(self, action: Action) -> dict[str, object]:
        """The review half both tools publish: scores from the first file with the worst review, then
        `score_file`, `reviewed_files`, and `file_actions` (ADR-0066 amendment). `tests_weight` is
        the caller's to add, where its payload places it."""
        actions: list[Action] = [half.action for half in self.halves]
        index = actions.index(worst_action(actions))
        payload = dict(self.halves[index].payload)
        payload["action"] = action
        payload["score_file"] = self.reviewed_paths[index]
        payload["reviewed_files"] = self.reviewed_paths
        payload["file_actions"] = file_actions(zip(self.reviewed_paths, actions, strict=True))
        if "citation_coverage" in self.halves[index].payload:
            payload["file_rows"] = [
                {
                    "path": path,
                    "action": half.action,
                    "citation_coverage": half.payload["citation_coverage"],
                    "reason_codes": [b["code"] for b in self.blockers if b.get("reviewed_file_index") == i],
                }
                for i, (path, half) in enumerate(zip(self.reviewed_paths, self.halves, strict=True))
            ]
        if self.context_omissions:
            payload["context_omissions"] = self.context_omissions
        return payload


def _wire_units(value: object) -> int:
    return length(stringify_compact(value))


def _file_context(
    index: int,
    peers: Sequence[tuple[dict[str, str], int]],
    sources: Sequence[tuple[Mapping[str, object], int]],
    limit: int,
) -> tuple[dict[str, object], int, list[str]]:
    """Pack complete supplemental records. Count the encoded keys, separators and UTF-16 text."""
    path = peers[index][0]["path"]
    context: dict[str, object] = {"path": "", "evidence": [], "related_files": []}
    used = _wire_units(context)
    path_units = _wire_units(path) - 2
    required = used + path_units
    omitted: list[str] = []
    if required <= limit:
        context["path"] = path
        used = required
    else:
        omitted.append("path")
    for field, items in (("evidence", sources), ("related_files", [*peers[:index], *peers[index + 1 :]])):
        packed: list[Mapping[str, object]] = []
        for position, (item, units) in enumerate(items):
            required += units + int(position > 0)
            cost = units + int(bool(packed))
            if used + cost <= limit:
                packed.append(item)
                used += cost
            elif field == "related_files":
                sibling = position if position < index else position + 1
                omitted.append(f"reviewed_files[{sibling}]")
            else:
                omitted.append(f"evidence[{position}]")
        context[field] = packed
    return context, required, omitted


async def review_file_list(
    files: Sequence[Mapping[str, str]],
    args: dict[str, Any],
    runtime: Runtime,
    settings: ReviewSettings,
    *,
    purpose: str,
    doc_units: int,
    evidence: Sequence[Mapping[str, object]] = (),
    gate_framing: str | None = None,
) -> FileListReview:
    """One review request per file under `doc_units`; a file over it is unreviewed (ADR-0066).

    Each fitting file is asked the plain rubric over its own documents through its own ledger, so a
    cut in one file is recorded without joining the files back into a string that would be cut.
    `purpose` is the asking tool's own framing of the state. The requests run concurrently and the
    results fold in input order (ADR-0066 amendment, 2026-10-15).
    """
    fitting = [dict(item) for item in files if length(item["patch"]) <= doc_units]
    unreviewed = [item["path"] for item in files if length(item["patch"]) > doc_units]
    asked: list[tuple[CapLedger, ReviewDocs, Evaluation] | None] = [None] * len(fitting)
    failures: list[tuple[int, Exception]] = []

    fan_out = anyio.CapacityLimiter(REVIEW_FANOUT)
    context_limit = min(doc_units, GATE.aggregate_evidence_units // max(1, len(fitting)))
    peers = [(file, _wire_units(file)) for file in fitting]
    sources = [(item, _wire_units(item)) for item in evidence]
    omissions: dict[int, dict[str, object]] = {}

    pointers: list[Citations] = []

    prepared: list[tuple[CapLedger, ReviewDocs, dict[str, object], dict[str, Question]]] = []
    for index, item in enumerate(fitting):
        ledger = CapLedger()
        docs = review_docs({**args, "diff": item["patch"]}, ledger, doc_units)
        questions = review_questions(gate_framing or "")
        limit = context_limit
        while True:
            extra, required, missing = _file_context(index, peers, sources, limit)
            state = {"purpose": purpose, "request": docs.request, "diff": docs.diff, "tests": docs.tests, **extra}
            if gate_framing is None:
                break
            try:
                check_gate_budget(state, questions)
            except GateBudgetError:
                if not (extra["path"] or extra["evidence"] or extra["related_files"]):
                    raise
                limit = min(limit, required) // 2
            else:
                break
        if missing:
            omissions[index] = {
                "path": item["path"],
                "reviewed_file_index": index,
                "required_units": required,
                "limit_units": limit,
                "omitted": missing,
            }
        if gate_framing is not None:
            pointers.append(add_citations(state, questions, [{"id": "diff", "kind": "diff", "text": docs.diff}]))
        prepared.append((ledger, docs, state, questions))

    async def review(index: int) -> None:
        ledger, docs, state, questions = prepared[index]
        try:
            async with fan_out:
                evaluation = await runtime.ask(state, questions)
        except Exception as error:
            # One failed file fails the call, as the sequential pass did. Cancel the siblings so no
            # paid review keeps running, and re-raise outside the group so the kernel maps the error
            # itself, not an ExceptionGroup around it.
            failures.append((index, error))
            task_group.cancel_scope.cancel()
            return
        asked[index] = (ledger, docs, evaluation)

    # One task per fitting file; `REVIEW_FANOUT` bounds how many ask at once, and the ADR-0069
    # process cap in `runtime.ask` still applies on top.
    async with anyio.create_task_group() as task_group:
        for index in range(len(fitting)):
            task_group.start_soon(review, index)
    if failures:
        raise min(failures, key=lambda failure: failure[0])[1]

    halves: list[ReviewHalf] = []
    evaluations: list[Evaluation] = []
    reviewed_paths: list[str] = []
    unhashed_tests = False
    truncated = False
    scopes: frozenset[CapScope] = frozenset()
    blockers: list[Blocker] = []
    for item, result in zip(fitting, asked, strict=True):
        assert result is not None  # every task either stored its result or recorded a failure
        ledger, docs, evaluation = result
        half = project_review(evaluation.answers, settings, ledger.context_cut)
        index = len(reviewed_paths)
        if gate_framing is None:
            reasons = review_blockers(half.payload)
        else:
            half, reasons = grounded_review(
                half, evaluation.answers, pointers[index], settings.thresholds.auto_accept, path=item["path"]
            )
            if half.action != "auto" and index in omissions:
                names = {
                    "path": item["path"],
                    **{f"evidence[{i}]": str(source["id"]) for i, source in enumerate(evidence)},
                    **{f"reviewed_files[{i}]": peer["path"] for i, peer in enumerate(fitting)},
                }
                missing_names = [names[key] for key in cast(list[str], omissions[index]["omitted"])]
                reasons.append(
                    {
                        "field": "context",
                        "code": "context_omitted",
                        "missing": missing_names,
                        "next_check": f"Resubmit {item['path']} with omitted context: {', '.join(missing_names)}.",
                    }
                )
        halves.append(half)
        for reason in reasons:
            target = f"{item['path']} (reviewed_files[{index}], {reason['field']})"
            blockers.append(
                {
                    **reason,
                    "path": item["path"],
                    "reviewed_file_index": index,
                    "next_check": f"{target}: {reason['next_check']}",
                }
            )
        evaluations.append(evaluation)
        reviewed_paths.append(item["path"])
        if docs.tests and not args.get("tests_sha256"):
            unhashed_tests = True
        truncated = truncated or ledger.context_cut
        scopes |= ledger.scopes
    blockers.extend(
        {**size_blocker("patch", units, doc_units), "path": item["path"]}
        for item in files
        if (units := length(item["patch"])) > doc_units
    )
    return FileListReview(
        halves,
        evaluations,
        reviewed_paths,
        unreviewed,
        unhashed_tests,
        truncated,
        scopes,
        blockers,
        [omissions[index] for index in sorted(omissions)],
    )


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
