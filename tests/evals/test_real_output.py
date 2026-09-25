"""Contract (ADR-0028): judgments and calibration rows over what the real Toolset returns.

Each tool runs through the real `Toolset` with the fake provider and the security stage's answer triad
(`tests/security/tools.py`): permissive answers are a clean decision, hostile answers are weak and
dressed up to claim authority, and empty answers carry nothing. The gold labels below are the ones
the permissive answers get right, keyed by the ids in `CASES`. For breadth, every successful recorded
parity call (`tests/support/fixtures.py`) also runs through `SCORERS` and `judgments`, except calls
tagged with a divergence: only untagged calls are byte-equal to what this server returns.
"""

import json
from typing import Any

import pytest

from evals.calibration.rows import CALIBRATED_TOOLS, calibration_rows
from evals.scorers.fields import Json, as_object, as_objects
from evals.scorers.tools import SCORERS, Example, Judgment, judgments
from jev_judge_mcp.tools import TOOLS
from tests.security.tools import BY_TOOL, CASES, auto_anywhere
from tests.support.fixtures import iter_calls
from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


GOLD: dict[str, Json] = {
    "jev_verify": {"claims": {"claim0": "verified"}},
    "jev_screen": {"injection": False, "skip": False},
    "jev_find": {"relevance": {"a": 2, "b": 0}, "verdict": "answered"},
    "jev_classify": {"labels": {"t1": "billing"}},
    "jev_decide": {"acceptable": ["redis"], "checks": {"redis#0": "supported", "memcached#0": "contradicted"}},
    "jev_rerank": {"relevance": {"a": 2, "b": 0}},
    "jev_compare": {"relation": "same_fact", "aspects": {"price": "same_fact"}},
    "jev_extract": {"fields": {"build": "ABC-124"}},
    "jev_review": {"defective": False},
    "jev_gate": {"safe": True, "claims": ["verified"], "reason_codes": ["accepted"]},
    "jev_score": {"level": 0},
}
PARAMS: dict[str, Json] = {"jev_screen": {"max_false_block_rate": 0.0}}

EMPTY_INVALID = {"jev_verify": 1, "jev_screen": 1, "jev_find": 1, "jev_review": 1, "jev_score": 1}
"""The scorers that report `invalid` (evals/README.md), each counting its one empty-answer row."""
AUTO_TIER = {"jev_verify", "jev_screen", "jev_classify", "jev_compare", "jev_extract", "jev_review", "jev_gate"}
"""Tools with an AUTO decision; find, rerank, decide, and score have none."""
ANSWERS = ("permissive", "hostile", "empty")


async def example_for(tool: str, answers: str) -> Example:
    case = BY_TOOL[tool]
    empty: dict[str, Any] = {}
    given = {"permissive": case.permissive, "hostile": case.hostile, "empty": empty}[answers]
    outcome = await call_tool(tool, case.arguments, given)
    assert not outcome.is_error, outcome.text
    return Example(f"{tool}-{answers}", "contract", case.arguments, GOLD[tool], outcome.payload)


def test_the_triad_covers_every_scorer() -> None:
    assert [case.tool for case in CASES] == list(SCORERS) == list(GOLD)


@pytest.mark.parametrize("tool", list(SCORERS))
async def test_permissive_answers_are_valid_correct_decisions(tool: str) -> None:
    example = await example_for(tool, "permissive")
    score = SCORERS[tool]([example], PARAMS.get(tool, {}))
    found = judgments(tool, [example])
    assert score.metrics.get("invalid", 0) == 0
    assert len(found) == score.n
    assert all(j.predicted is not None and j.correct for j in found), found
    assert all(j.auto for j in found) is (tool in AUTO_TIER)


@pytest.mark.parametrize("tool", list(SCORERS))
async def test_empty_answers_are_invalid_where_the_docs_say(tool: str) -> None:
    example = await example_for(tool, "empty")
    score = SCORERS[tool]([example], PARAMS.get(tool, {}))
    assert score.metrics.get("invalid") == EMPTY_INVALID.get(tool)
    found = judgments(tool, [example])
    assert not any(j.auto for j in found)
    assert {j.predicted for j in found} == ({"unsafe"} if tool == "jev_gate" else {None})  # gate fails closed: escalate


@pytest.mark.parametrize("tool", list(SCORERS))
async def test_hostile_answers_never_read_as_auto(tool: str) -> None:
    example = await example_for(tool, "hostile")
    assert not any(j.auto for j in judgments(tool, [example]))


@pytest.mark.parametrize("answers", ANSWERS)
@pytest.mark.parametrize("tool", CALIBRATED_TOOLS)
async def test_a_decision_always_has_a_calibration_row(tool: str, answers: str) -> None:
    example = await example_for(tool, answers)
    found = judgments(tool, [example])
    rows = calibration_rows(tool, [example])
    assert {j.key for j in found if j.predicted is not None} <= set(rows)
    assert rows == {j.key: (j.score, j.correct) for j in found if j.score is not None}


def test_find_and_review_correctness_encodings() -> None:
    """Find is right when its top id is any relevant one; review's scored claim is 'safe to apply'."""
    assert Judgment("k", frozenset({"a", "b"}), "b", 0.9, False).correct
    assert not Judgment("k", frozenset[object](), "a", 0.9, False).correct
    assert not Judgment("k", "defective", "safe", 0.9, True).correct


# --- recorded parity fixtures (T44) ------------------------------------------------------------------


def _ids(output: Json, key: str) -> list[str]:
    return [str(item.get("id")) for item in as_objects(output.get(key))]


def gold_from(tool: str, output: Json) -> Json:
    """A structural gold keyed by the result's own ids: fixture arguments may be string shorthand."""
    match tool:
        case "jev_verify":
            return {"claims": {i: "verified" for i in _ids(output, "results")}}
        case "jev_screen":
            return {"injection": False}
        case "jev_find":
            return {"relevance": {i: 1 for i in _ids(output, "top")[:1]}}
        case "jev_rerank":
            return {"relevance": {i: 1 for i in _ids(output, "ranked")[:1]}}
        case "jev_classify":
            return {"labels": {i: "billing" for i in _ids(output, "results")}}
        case "jev_decide":
            return {"acceptable": []}
        case "jev_compare":
            return {"relation": "same_fact"}
        case "jev_extract":
            return {"fields": {i: None for i in _ids(output, "results")}}
        case "jev_review":
            return {"defective": False}
        case "jev_score":
            return {"level": output.get("nearest_level")}  # structural: no jev_score parity fixture exists
        case "jev_gate":
            return {
                "safe": True,
                "claims": ["verified"] * len(as_objects(as_object(output.get("verification")).get("results"))),
            }
        case _:
            raise KeyError(tool)


def recorded_examples() -> dict[str, list[Example]]:
    """Every successful recorded call, parsed and grouped by tool.

    Sanctioned divergences still carry the old keys the scorer reads. Skipping them
    would drop jev_gate, jev_verify, and jev_review once every call is tagged.
    """
    grouped: dict[str, list[Example]] = {}
    for call in iter_calls():
        tool, result = call.payload["tool"], call.payload["result"]
        if result.get("isError"):
            continue
        output = json.loads(result["content"][0]["text"])
        grouped.setdefault(tool, []).append(
            Example(call.id, "parity", call.payload["arguments"], gold_from(tool, output), output)
        )
    return grouped


RECORDED = recorded_examples()


def test_the_recorded_fixtures_reach_every_scorer() -> None:
    # The frozen ten must all be reached by recorded parity calls. An ADR-0048 extension tool has
    # none by design — the reference never published it — so it is exempt exactly when it sits
    # after the snapshot ten in the registry.
    snapshot_ten = {tool.name for tool in TOOLS[:10]}
    extensions = {tool.name for tool in TOOLS} - snapshot_ten
    assert snapshot_ten <= set(RECORDED)
    assert set(RECORDED) <= set(SCORERS)
    assert set(SCORERS) - set(RECORDED) <= extensions


@pytest.mark.parametrize("tool", [tool for tool in SCORERS if tool in RECORDED])
def test_recorded_output_scores_and_joins(tool: str) -> None:
    """Recorded verify output keeps a verdict whose confidence is null (never AUTO), so the row
    invariant here is AUTO ⇒ row, not decision ⇒ row."""
    examples = RECORDED[tool]
    score = SCORERS[tool](examples, PARAMS.get(tool, {}))
    found = judgments(tool, examples)
    assert len(found) == score.n
    for example in examples:  # screen's AUTO is its `pass` recommendation, which carries no "auto" value
        assert (
            tool == "jev_screen" or auto_anywhere(example.output) or not any(j.auto for j in judgments(tool, [example]))
        ), example.id
    if tool in CALIBRATED_TOOLS:
        rows = calibration_rows(tool, examples)
        assert {j.key for j in found if j.auto} <= set(rows)
