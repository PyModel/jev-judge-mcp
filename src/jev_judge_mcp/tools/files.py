"""The multi-file diff path jev_review and jev_gate share (ADR-0066): the `[{path, patch}]` shape, its
two budgets, one review request per fitting file, and the frame both tools publish over the result.

The two tools differ only in how they refuse a budget (jev_review raises it typed, jev_gate returns
the isError payload), in the purpose text each sends, and in what jev_gate adds after the files
(claim verification groups); everything the per-file pass knows lives here once.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import anyio

from jev_judge_mcp.domain import Question
from jev_judge_mcp.gate_budget import fits_budget
from jev_judge_mcp.gate_diagnostics import Blocker, size_blocker
from jev_judge_mcp.limits import GATE, GateCaps
from jev_judge_mcp.policy import Action, worst_action
from jev_judge_mcp.providers import Evaluation
from jev_judge_mcp.serialize import stringify_compact
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import Runtime, ToolError, code_of
from jev_judge_mcp.tools.gate_evidence import Citations, add_citations, grounded_review
from jev_judge_mcp.tools.review_half import (
    ReviewDocs,
    ReviewHalf,
    ReviewSettings,
    project_review,
    review_docs,
    review_questions,
)
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
    truncated: bool
    """Whether any file's documents were cut; an unreviewed file is `partial`, never a cut."""
    scopes: frozenset[CapScope]

    blockers: list[Blocker]
    context_omissions: list[dict[str, object]]

    def review_half(self, action: Action, *, held_indices: frozenset[int] = frozenset()) -> dict[str, object]:
        """The review half both tools publish: scores from the first file with the worst review, then
        `score_file`, `reviewed_files`, and `file_actions` (ADR-0066 amendment). `tests_weight` is
        the caller's to add, where its payload places it."""
        actions: list[Action] = [
            "review" if half.action == "auto" and index in held_indices else half.action
            for index, half in enumerate(self.halves)
        ]
        if not actions:
            return {"action": action, "reviewed_files": [], "file_actions": {}}
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
                    "action": actions[i],
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


def file_review_state(docs: ReviewDocs, purpose: str) -> dict[str, object]:
    return {
        "purpose": purpose,
        "request": docs.request,
        "diff": docs.diff,
        "tests": docs.tests,
        "path": "",
        "evidence": [],
        "related_files": [],
    }


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
    asked: list[Evaluation | None] = [None] * len(fitting)
    failures: list[tuple[int, Exception]] = []

    context_limit = min(doc_units, GATE.aggregate_evidence_units // max(1, len(fitting)))
    peers = [(file, _wire_units(file)) for file in fitting]
    sources = [(item, _wire_units(item)) for item in evidence]
    omissions: dict[int, dict[str, object]] = {}

    pointers: list[Citations] = []

    prepared: list[tuple[CapLedger, dict[str, object], dict[str, Question]]] = []
    for index, item in enumerate(fitting):
        ledger = CapLedger()
        docs = review_docs({**args, "diff": item["patch"]}, ledger, doc_units)
        questions = review_questions(gate_framing or "")
        limit = context_limit
        while True:
            extra, required, missing = _file_context(index, peers, sources, limit)
            state = {**file_review_state(docs, purpose), **extra}
            if fits_budget(state, questions) or not (extra["evidence"] or extra["related_files"]):
                break
            limit = min(limit, required) // 2
        if missing:
            omissions[index] = {
                "path": item["path"],
                "reviewed_file_index": index,
                "required_units": required,
                "limit_units": limit,
                "omitted": missing,
            }
        pointers.append(add_citations(state, questions, [{"id": "diff", "kind": "diff", "text": docs.diff}]))
        prepared.append((ledger, state, questions))

    async def review(index: int) -> None:
        _, state, questions = prepared[index]
        try:
            async with runtime.file_fanout:
                asked[index] = await runtime.ask(state, questions)
        except Exception as error:
            if gate_framing is not None and code_of(error) == "input_too_large":
                return  # Gate still attempts claim verification and projects this file review as missing.
            # One failed file fails the call, as the sequential pass did. Cancel the siblings so no
            # paid review keeps running, and re-raise outside the group so the kernel maps the error
            # itself, not an ExceptionGroup around it.
            failures.append((index, error))
            task_group.cancel_scope.cancel()

    async with anyio.create_task_group() as task_group:
        for index in range(len(fitting)):
            task_group.start_soon(review, index)
    if failures:
        raise min(failures, key=lambda failure: failure[0])[1]

    halves: list[ReviewHalf] = []
    evaluations: list[Evaluation] = []
    reviewed_paths: list[str] = []
    truncated = False
    scopes: frozenset[CapScope] = frozenset()
    blockers: list[Blocker] = []
    for item, evaluation in zip(fitting, asked, strict=True):
        index = len(reviewed_paths)
        ledger, _, _ = prepared[index]
        answers = evaluation.answers if evaluation is not None else {}
        half = project_review(answers, settings, ledger.context_cut)
        half, reasons = grounded_review(
            half, answers, pointers[index], settings.thresholds.auto_accept, path=item["path"]
        )
        if evaluation is None:
            reasons[0]["next_check"] = f"Split {item['path']} by hunk and retry the file review."
        if gate_framing is not None and half.action != "auto" and index in omissions:
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
        if evaluation is not None:
            evaluations.append(evaluation)
        reviewed_paths.append(item["path"])
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
        truncated,
        scopes,
        blockers,
        [omissions[index] for index in sorted(omissions)],
    )
