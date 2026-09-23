"""Contract: the scorers and calibration rows read what the real Toolset returns.

`test_tool_scorers.py` feeds the scorers hand-written results, so a renamed or moved result field
would leave it green while every live score silently turned into `None` or `invalid`. Here each tool
runs through the real `Toolset` on its first bench150 item, with a fake provider whose answers are
confident and deterministic; the parsed result text is scored, and each assertion pins a metric that
only comes out as expected when the scorer found the field it reads.
"""

import json
from collections.abc import Mapping
from typing import Any, cast, override

import pytest

from evals.bench.items import load_items
from evals.calibration.rows import CALIBRATED_TOOLS, calibration_rows
from evals.scorers.fields import Json
from evals.scorers.tools import SCORERS, Example
from jev_judge_mcp.domain import JsonValue, Usage
from jev_judge_mcp.providers import Evaluation
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from tests.support.jev import FakeProvider, text_of

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


INPUTS: dict[str, Json] = {}
for _item in load_items():
    INPUTS.setdefault(_item.tool, _item.input)
INPUTS["jev_score"] = {
    "subject": "Regression risk of the rename.",
    "levels": ["minor risk", "major risk", "severe risk"],
}
"""jev_score has no bench150 corpus (ADR-0048 extension tool): one representative input stands in
so the contract run still exercises the published tool end to end."""


class ConfidentProvider(FakeProvider):
    """Choices pick a `prefer`red criterion, else the first, at 0.99; scores are the best (2); nouls 0.99
    unless overridden by question id."""

    def __init__(self, nouls: Mapping[str, float], prefer: tuple[str, ...]) -> None:
        super().__init__({})
        self.nouls = dict(nouls)
        self.prefer = prefer

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        answers: dict[str, Any] = {}
        for question_id, raw in questions.items():
            question = cast(dict[str, Any], raw)
            match question["type"]:
                case "noul":
                    answers[question_id] = {"noul": self.nouls.get(question_id, 0.99), "confidence": 0.99}
                case "choice":
                    keys = list(question["criteria"])
                    pick = next((key for key in self.prefer if key in keys), keys[0])
                    rest = 0.01 / max(1, len(keys) - 1)
                    probabilities = {key: 0.99 if key == pick else rest for key in keys}
                    answers[question_id] = {"choice": pick, "confidence": 0.99, "probabilities": probabilities}
                case "score":
                    # A full distribution over the question's own levels: the rubric validator
                    # (jev_score) requires it, and the fixed-rubric tools tolerate the extra field.
                    levels = list(question["criteria"])
                    top = min(2, len(levels) - 1)
                    rest = 0.01 / max(1, len(levels) - 1)
                    probabilities = {str(i): (0.99 if i == top else rest) for i in range(len(levels))}
                    answers[question_id] = {"score": top, "confidence": 0.99, "probabilities": probabilities}
                case _:
                    answers[question_id] = {"score": 2, "confidence": 0.99}
        return Evaluation(answers, Usage(1, 1), self.name, model)


async def real_output(tool: str, nouls: Mapping[str, float] | None = None, prefer: tuple[str, ...] = ()) -> Json:
    provider = ConfidentProvider(nouls or {}, prefer)
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    try:
        result = await toolset.call(tool, INPUTS[tool])
    finally:
        await toolset.aclose()
    assert not result.is_error, text_of(result)
    return json.loads(text_of(result))


def score(tool: str, gold: Json, output: Json, params: Json | None = None) -> dict[str, Any]:
    example = Example(f"{tool}-0", "contract", INPUTS[tool], gold, output)
    return SCORERS[tool]([example], params or {}).metrics


def _first_id(tool: str, key: str) -> str:
    return str(INPUTS[tool][key][0]["id"])


def gold_for(tool: str) -> Json:
    """A gold label the confident answers get right, keyed by ids from the real input."""
    match tool:
        case "jev_verify":
            return {"claims": {"claim0": "verified"}}
        case "jev_screen":
            return {"injection": False, "skip": True}
        case "jev_find" | "jev_rerank":
            return {"relevance": {_first_id(tool, "candidates"): 2}, "verdict": "answered"}
        case "jev_classify":
            return {"labels": {_first_id(tool, "items"): _first_id(tool, "classes")}}
        case "jev_decide":
            return {"acceptable": [_first_id(tool, "candidates")]}
        case "jev_compare":
            return {"relation": "same_fact"}
        case "jev_extract":
            return {"fields": {_first_id(tool, "fields"): None}}
        case "jev_review":
            return {"defective": False}
        case "jev_gate":
            return {"safe": True, "claims": ["verified"] * len(INPUTS[tool]["claims"]), "reason_codes": ["accepted"]}
        case "jev_score":
            return {"level": 2}  # the confident score answer tops a 3-level rubric at level 2
        case _:
            raise KeyError(tool)


# Screen: a low substance noul is the only way its recommendation reaches `skip`.
NOULS: dict[str, dict[str, float]] = {"jev_screen": {"injection": 0.01, "substance": 0.01}}


def test_every_scorer_is_covered() -> None:
    assert list(SCORERS) == [tool.name for tool in TOOLS]
    assert set(INPUTS) == set(SCORERS)


async def test_verify_reads_verdict_action_probabilities_and_confidence() -> None:
    m = score("jev_verify", gold_for("jev_verify"), await real_output("jev_verify"))
    assert (m["selective_accuracy"], m["auto_coverage"], m["invalid"]) == (1.0, 1.0, 0)
    assert m["brier"] is not None
    assert m["ece"] == pytest.approx(0.01)


async def test_screen_reads_injection_probability_and_recommendation() -> None:
    output = await real_output("jev_screen", NOULS["jev_screen"])
    m = score("jev_screen", gold_for("jev_screen"), output, {"max_false_block_rate": 0.0})
    assert (m["invalid"], m["skip_precision"]) == (0, 1.0)


async def test_find_reads_top_ids_exists_and_verdict() -> None:
    m = score("jev_find", gold_for("jev_find"), await real_output("jev_find"))
    assert (m["recall_at_1"], m["mrr"], m["verdict_accuracy"], m["invalid"]) == (1.0, 1.0, 1.0, 0)


async def test_rerank_reads_ranked_ids() -> None:
    m = score("jev_rerank", gold_for("jev_rerank"), await real_output("jev_rerank"))
    assert m["mrr"] == 1.0


async def test_classify_reads_classification_and_decision() -> None:
    m = score("jev_classify", gold_for("jev_classify"), await real_output("jev_classify"))
    assert (m["selective_accuracy_auto"], m["auto_coverage"], m["micro_f1"]) == (1.0, 1.0, 1.0)


async def test_decide_reads_the_escape_flag() -> None:
    output = await real_output("jev_decide")
    assert score("jev_decide", gold_for("jev_decide"), output)["escape_hatch_accuracy"] == 1.0
    assert score("jev_decide", {"acceptable": []}, output)["overdecision_rate"] == 1.0


async def test_compare_reads_the_overall_relation() -> None:
    output = await real_output("jev_compare", prefer=("contradicts",))
    m = score("jev_compare", {"relation": "contradicts", "perturbation": "numeric"}, output)
    assert m["perturbed_contradiction_recall"] == 1.0


async def test_extract_reads_value_and_status() -> None:
    tool = "jev_extract"
    output = await real_output(tool)
    field = _first_id(tool, "fields")
    value = output["results"][0]["value"]
    assert score(tool, {"fields": {field: value}}, output)["exact_match"] == 1.0
    assert score(tool, {"fields": {field: value}}, output)["hallucinated_values"] == 0
    absent = score(tool, gold_for(tool), await real_output(tool, prefer=("none_of_them",)))
    assert (absent["not_found_precision"], absent["not_found_recall"]) == (1.0, 1.0)


async def test_review_reads_action_and_safe_to_apply() -> None:
    m = score("jev_review", gold_for("jev_review"), await real_output("jev_review"))
    assert (m["auto"], m["p_defective_given_auto"], m["invalid"]) == (1, 0.0, 0)


async def test_gate_reads_action_and_nested_claim_verdicts() -> None:
    output = await real_output("jev_gate")
    m = score("jev_gate", gold_for("jev_gate"), output)
    assert (m["auto"], m["false_auto_rate"], m["claim_macro_f1"], m["reason_code_accuracy"]) == (1, 0.0, 1.0, 1.0)


@pytest.mark.parametrize("tool", CALIBRATED_TOOLS)
async def test_calibration_rows_read_the_real_score(tool: str) -> None:
    output = await real_output(tool, NOULS.get(tool))
    gold = gold_for(tool)
    if tool == "jev_extract":
        gold = {"fields": {_first_id(tool, "fields"): output["results"][0]["value"]}}
    rows = calibration_rows(tool, [Example(f"{tool}-0", "contract", INPUTS[tool], gold, output)])
    assert list(rows.values()) == [(pytest.approx(0.99), True)]
