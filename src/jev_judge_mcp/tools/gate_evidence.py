"""Evidence pointers for actionable completion judgments (ADR-0082)."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import zip_longest
from typing import Any, TypedDict

from jev_judge_mcp.domain import ChoiceQuestion, Question
from jev_judge_mcp.gate_budget import GateBudgetError, check_gate_budget
from jev_judge_mcp.gate_diagnostics import Blocker, review_blockers
from jev_judge_mcp.responses import EXCERPT_UNITS
from jev_judge_mcp.text import head
from jev_judge_mcp.tools.observed import validate_choice
from jev_judge_mcp.tools.review_half import ANTI_INJECTION, ReviewHalf

KINDS = {
    "none": "No concrete defect or explicit refusal is demonstrated. Includes uncertainty, missing tests or context.",
    "wrong_result": "The changed code produces an incorrect result for a specific input or requirement.",
    "state_change": "The change performs the wrong state transition, loses data or violates a state invariant.",
    "error_handling": "A changed failure path crashes, suppresses a failure or reports success incorrectly.",
    "safety_refusal": "An explicit safety rule prohibits this operation. Risk or uncertainty alone is not a refusal.",
}

_CHECKS = {
    "wrong_result": "Check the incorrect result",
    "state_change": "Check the incorrect state transition",
    "error_handling": "Check the mishandled failure",
    "safety_refusal": "Resolve the explicit safety refusal",
}

_MAX_LINES = 32


class Line(TypedDict):
    source: str
    line: int
    text: str


_PASS_CLAIM = re.compile(
    r"(?:the |all |all the )?(?:tests?|test suite|suite)(?: all)? (?:pass(?:ed|es)?|succeed(?:ed|s)?)[.!]?", re.I
)
_PASS_LINE = re.compile(r"(?:=+\s*)?(?:\d+ passed(?: in [\d.]+s)?|pass(?:ed)?|ok)(?:\s*=+)?", re.I)


def contradicting_line(claim: str, line: Line | None) -> Line | None:
    if line and _PASS_CLAIM.fullmatch(claim.strip()) and _PASS_LINE.fullmatch(line["text"].strip()):
        return None
    return line


def _lines(item: Mapping[str, object]) -> list[Line]:
    source = str(item["id"])
    is_patch = item.get("kind") == "diff"
    lines: list[Line] = []
    for number, raw in enumerate(str(item["text"]).split("\n"), 1):
        if is_patch and (not raw.startswith(("+", "-")) or raw.startswith(("+++ ", "--- "))):
            continue
        excerpt = head(raw, EXCERPT_UNITS)
        if (excerpt[1:] if is_patch else excerpt).strip():
            lines.append({"source": source, "line": number, "text": excerpt})
    return lines


@dataclass(frozen=True)
class Citations:
    lines: dict[str, Line]
    available: int

    def select(self, raw: object, threshold: float) -> Line | None:
        if not self.lines:
            return None
        answer = validate_choice(raw, ["none", *self.lines])
        if answer is None or answer.confidence is None or answer.confidence < threshold:
            return None
        return self.lines.get(answer.choice)

    def coverage(self) -> dict[str, int]:
        return {"offered": len(self.lines), "available": self.available}


def add_citations(
    state: dict[str, Any],
    questions: dict[str, Question],
    items: Sequence[Mapping[str, object]],
    *,
    claim_count: int | None = None,
) -> Citations:
    """Fit optional pointers after the mandatory preflight. Never evict primary evidence to fit them."""
    groups: list[list[Line]] = []
    for item in items:
        if item.get("kind") == "caller_note":
            continue
        group = _lines(item)
        if claim_count is not None and item.get("kind") == "diff":
            group = [line for line in group if line["text"].startswith("+")]
        groups.append(group)
    available = sum(map(len, groups))
    # Sample throughout long documents and interleave sources so a long first log cannot consume every slot.
    sampled = [
        [group[i * (len(group) - 1) // (_MAX_LINES - 1)] for i in range(_MAX_LINES)]
        if len(group) > _MAX_LINES
        else group
        for group in groups
    ]
    candidates = [line for batch in zip_longest(*sampled) for line in batch if line is not None][:_MAX_LINES]
    field = "patch_lines" if claim_count is None else "counterevidence_lines"
    while candidates:
        lines = {f"L{i}": line for i, line in enumerate(candidates, 1)}
        criteria = {"none": "No listed line demonstrates the issue.", **dict.fromkeys(lines)}
        additions: dict[str, Question] = {}
        if claim_count is None:
            framing = " Inspect the target file and its cross-file contracts."
            additions["defect_kind"] = ChoiceQuestion(
                "Which specific failure does a changed line introduce? Select none unless demonstrated."
                + framing
                + ANTI_INJECTION,
                KINDS,
            )
            additions["defect_line"] = ChoiceQuestion(
                "Which patch_lines entry most directly demonstrates that failure or explicit refusal? "
                "Select none for uncertainty, a missing test, or an innocent line." + framing + ANTI_INJECTION,
                criteria,
            )
        else:
            for i in range(claim_count):
                additions[f"counterexample_{i}"] = ChoiceQuestion(
                    f"Which counterevidence_lines entry explicitly proves the opposite of claims[{i}] at its "
                    "stated time and scope? Positive output from a passing run does not contradict that run passing. "
                    "Missing evidence, a diff header, or an unrelated failure is none." + ANTI_INJECTION,
                    criteria,
                )
        try:
            check_gate_budget({**state, field: lines}, {**questions, **additions})
        except GateBudgetError:
            candidates = candidates[::2] if len(candidates) > 1 else []
            continue
        questions.update(additions)
        state[field] = lines
        return Citations(lines, available)
    return Citations({}, available)


def grounded_review(
    half: ReviewHalf, answers: Mapping[str, object], citations: Citations, threshold: float, *, path: str = ""
) -> tuple[ReviewHalf, list[Blocker]]:
    blockers = review_blockers(half.payload)
    if blockers and not half.invalid:
        documentation = path.lower().endswith((".md", ".rst", ".txt"))
        anchor = max(
            citations.lines.values(),
            key=lambda line: (
                line["text"].startswith("+"),
                not documentation and line["text"][1:].lstrip().startswith(("return ", "raise ", "if ", "assert ")),
            ),
            default=None,
        )
        if anchor:
            location = f"patch line {anchor['line']}"
            for blocker in blockers:
                if documentation:
                    blocker["next_check"] = f"Verify the changed statement at {location}: {anchor['text'][1:].strip()}"
                else:
                    check = blocker["next_check"].removesuffix(".")
                    blocker["next_check"] = f"{check} ({location}: {anchor['text']})."
    kind = validate_choice(answers.get("defect_kind"), KINDS) if citations.lines else None
    selected = kind.choice if kind and kind.confidence is not None and kind.confidence >= threshold else "none"
    line = citations.select(answers.get("defect_line"), threshold)
    action = half.action
    if selected != "none" and line is not None:
        action = "escalate"
        blockers.insert(
            0,
            {
                "field": "defect",
                "code": selected,
                "evidence_line": line,
                "next_check": f"{_CHECKS[selected]} at patch line {line['line']} ({line['text']}).",
            },
        )
    elif half.invalid:
        # A required rubric never produced a usable result. Preserve the explicit fail-closed protocol.
        for blocker in blockers:
            blocker.update(
                safety_refusal=True, evidence_line={"source": "provider", "field": blocker["field"], "value": None}
            )
    elif action == "escalate" or (kind is not None and kind.choice != "none"):
        action = "review"
        if kind is not None and kind.choice != "none":
            blockers.insert(
                0,
                {
                    "field": "defect_line",
                    "code": "defect_line_missing",
                    "next_check": f"Supply the exact changed line demonstrating {kind.choice}.",
                },
            )
    payload = {**half.payload, "action": action, "citation_coverage": citations.coverage()}
    return ReviewHalf(payload, action, half.invalid), blockers
