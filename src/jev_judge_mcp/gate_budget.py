"""Conservative preflight for both Jev context windows, including serialized question criteria."""

from collections.abc import Mapping

from jev_judge_mcp.domain import Question, questions_to_wire
from jev_judge_mcp.serialize import stringify_compact

STATE_QUESTION_BYTES = 30_000
REQUEST_BYTES = 60_000
SPLIT_GUIDANCE = "Split the diff by file or hunk and submit each claim with its supporting evidence."


class GateBudgetError(ValueError):
    """This gate would exceed a context window before the provider can judge it."""


def check_gate_budget(state: object, questions: Mapping[str, Question]) -> None:
    # Compact UTF-8 bytes conservatively budget for byte-level tokenization. Leave room for
    # provider framing beneath the documented 32k and 64k token windows.
    state_bytes = len(stringify_compact(state).encode("utf-8"))
    wire = questions_to_wire(questions)
    # Removing just the outer braces leaves one complete encoded key:value pair per question.
    members = [len(stringify_compact({key: value}).encode("utf-8")) - 2 for key, value in wire.items()]
    envelope = len(b'{"state":,"questions":{}}') + state_bytes
    for size, limit, label in (
        (envelope + max(members), STATE_QUESTION_BYTES, "state plus longest question"),
        (envelope + sum(members) + len(members) - 1, REQUEST_BYTES, "whole request"),
    ):
        if size > limit:
            raise GateBudgetError(f"{label} uses {size} UTF-8 bytes, limit {limit}. {SPLIT_GUIDANCE}")
