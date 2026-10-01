"""jev_ask, the ask extension tool (ADR-0077, divergence `ask-tool-extension`).

The schema pinning, the typed union refusals before any I/O, the payload shape, and the teaching
description are owned here (the `test_file_judge.py` shape); the assembler, gate ordering, command
runner, and refusal matrix are `tests/unit/test_ask.py`, the malformed-answer matrix is
`test_fail_closed.py`'s Case, and the hostile-input replay is the security suite's tool case.
"""

from typing import Any, cast

import pytest

from jev_judge_mcp.limits import ASK, FILE_JUDGE
from jev_judge_mcp.tools import TOOLS
from jev_judge_mcp.tools.ask import DEFINITION
from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio

NOTES = "tests/fixtures/file_judge/notes.txt"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def score_question(qid: str = "severity") -> dict[str, Any]:
    return {
        qid: {"type": "score", "instructions": "How severe is the state?", "criteria": ["minor", "major"]},
    }


def ask_args() -> dict[str, Any]:
    return {"questions": score_question(), "state": "The payment service retries twice before failing over."}


async def test_the_payload_keys_answers_by_the_caller_ids_and_summarizes_the_state() -> None:
    outcome = await call_tool(
        "jev_ask",
        ask_args(),
        {"severity": {"score": 0.5, "probabilities": {"0": 0.5, "1": 0.5}, "confidence": 0.9}},
    )
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["tool"] == "jev_ask"
    assert payload["provider"] == "compatible"
    assert list(payload["answers"]) == ["severity"]
    assert payload["answers"]["severity"] == {
        "score": 0.5,
        "nearest_level": 0,
        "probabilities": {"0": 0.5, "1": 0.5},
        "confidence": 0.9,
        "status": "ok",
    }
    assert payload["status"] == "ok"
    assert payload["state_summary"]["own"] > 0
    assert payload["state_summary"]["files"] == 0
    assert payload["state_summary"]["output"] == 0
    assert payload["state_summary"]["skipped"] == []
    assert payload["state_summary"]["units"] >= payload["state_summary"]["own"]
    assert payload["usage"] == {"input_tokens": 1, "output_tokens": 1}


async def test_three_kinds_in_one_call_each_answer_fail_closed_per_question() -> None:
    args: dict[str, Any] = {
        "questions": {
            "gate": {"type": "noul", "instructions": "Is the state about payments?", "criteria": {}},
            "where": {
                "type": "choice",
                "instructions": "Where does the port come from?",
                "criteria": {"config": "hard-coded", "env": "environment", "other": "elsewhere"},
            },
            **score_question("severity"),
        },
        "state": "port 8080, config.yaml",
    }
    answers: dict[str, Any] = {
        "gate": {"noul": 0.93},
        "where": {"choice": "config", "probabilities": {"config": 0.9, "env": 0.05, "other": 0.05}, "confidence": 0.7},
        "severity": {"score": 99},
    }
    outcome = await call_tool("jev_ask", args, answers)
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["answers"]["gate"] == {"noul": 0.93, "status": "ok"}
    assert payload["answers"]["where"]["choice"] == "config"
    # The malformed score answer fails closed alone; the other two keep their verdicts.
    assert payload["answers"]["severity"] == {
        "score": None,
        "nearest_level": None,
        "probabilities": None,
        "confidence": None,
        "status": "invalid_response",
    }
    assert payload["status"] == "invalid_response"
    assert payload["usage"] == {"input_tokens": 1, "output_tokens": 1}


async def test_the_state_is_named_fields_and_the_questions_ride_the_wire() -> None:
    outcome = await call_tool(
        "jev_ask", ask_args(), {"severity": {"score": 0.5, "probabilities": {"0": 0.5, "1": 0.5}}}
    )
    assert not outcome.is_error
    state, questions = outcome.requests[0]
    request_state = cast(dict[str, object], state)
    assert "retries twice" in cast(str, request_state["state"])
    assert list(questions) == ["severity"]
    wire = cast(dict[str, object], questions["severity"])
    assert wire == {
        "type": "score",
        "instructions": "How severe is the state?",
        "criteria": ["minor", "major"],
    }
    assert list(wire) == ["type", "instructions", "criteria"]


async def test_server_read_paths_become_state_and_refused_paths_are_skipped_typed(
    tmp_path: Any, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("retries twice", encoding="utf-8")
    args: dict[str, Any] = ask_args() | {"paths": ["notes.txt", "missing.txt", ".env"]}
    outcome = await call_tool("jev_ask", args, {"severity": {"score": 0.5, "probabilities": {"0": 0.5, "1": 0.5}}})
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["state_summary"]["files"] == 1
    assert payload["state_summary"]["skipped"] == [
        {"path": "missing.txt", "reason": "not_found"},
        {"path": ".env", "reason": "secret_file"},
    ]
    state = cast(dict[str, object], outcome.requests[0][0])
    assert "retries twice" in cast(str, state["notes.txt"])


async def test_every_question_shape_refusal_is_typed_and_makes_no_request() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        ({}, "at least 1 question"),
        ({f"q{i}": score_question("q")["q"] for i in range(ASK.questions_max + 1)}, "at most 20 questions"),
        ({"q": {"type": "boolean", "instructions": "x", "criteria": {}}}, "type must be one of noul, choice, score"),
        ({"q": {"type": "score", "criteria": ["a", "b"]}}, "instructions"),
        ({"q": {"type": "score", "instructions": "x"}}, "requires `criteria`"),
        ({"q": {"type": "noul", "instructions": "x", "criteria": {"maybe": "y"}}}, "true"),
        ({"q": {"type": "score", "instructions": "x", "criteria": ["only"]}}, "2-10 levels"),
        ({"q": {"type": "score", "instructions": "x", "criteria": ["a", "b" * 201]}}, "at most 200 units"),
        ({"q": "a raw string, not a typed question"}, "object with `type`"),
        ({"q" * 65: score_question("q")["q"]}, "at most 64 units"),
    ]
    for questions, needle in cases:
        outcome = await call_tool("jev_ask", {"questions": questions, "state": "s"}, {"q": {"noul": 0.5}})
        assert outcome.is_error, needle
        assert outcome.code == "invalid_arguments", needle
        assert needle in outcome.text, needle
        assert outcome.requests == [], needle


async def test_no_state_refuses_before_any_call() -> None:
    outcome = await call_tool("jev_ask", {"questions": score_question()}, {"severity": {"score": 0.5}})
    assert outcome.is_error
    assert outcome.code == "invalid_arguments"
    assert "no state" in outcome.text
    assert outcome.requests == []


def test_the_schema_carries_the_adr_owned_caps_and_keeps_questions_whole() -> None:
    schema = DEFINITION.input_schema
    questions = schema["properties"]["questions"]
    assert questions["type"] == "object"
    assert "properties" not in questions  # a keep-whole map: per-question shapes are tool-level
    assert schema["properties"]["state"]["maxLength"] == ASK.state_units_max
    assert schema["properties"]["paths"]["maxItems"] == ASK.files_max
    assert schema["properties"]["command"]["minLength"] == 1
    assert schema["required"] == ["questions"]
    assert schema["additionalProperties"] is False


def test_the_description_teaches_the_union_the_rules_and_the_gate() -> None:
    description = DEFINITION.description
    assert description is not None
    assert "noul" in description and "choice" in description and "score" in description
    assert "always " in description and "`other` option" in description
    assert "questions in one request cannot see each other's" in description
    assert "Not for exact lookups, counting, math, or " in description
    assert (
        "Jev is invoked when an unresolved judgment earns a model decision. Deterministic evidence "
        "takes precedence; Jev is not a mandatory ceremony."
    ) in description
    assert "does not sandbox" in description
    assert "command_refused" in description and "split suggestion" in description
    assert "fail-closed per question" in description


def test_jev_ask_is_published_last_and_the_frozen_prefix_is_untouched() -> None:
    names = tuple(tool.name for tool in TOOLS)
    assert names[:11] == (
        "jev_verify",
        "jev_screen",
        "jev_find",
        "jev_classify",
        "jev_decide",
        "jev_rerank",
        "jev_compare",
        "jev_extract",
        "jev_review",
        "jev_gate",
        "jev_score",
    )
    assert names[11] == "jev_file_judge"
    assert names[12] == "jev_ask"
    assert names[-1] == "jev_files_judge"


def test_the_question_text_bounds_are_the_file_tool_convention() -> None:
    """One convention for caller-written question text: the ADR-0077 file-tool bounds."""
    assert FILE_JUDGE.instructions_units_max == 2_000
    questions = DEFINITION.input_schema["properties"]["questions"]
    assert f"{FILE_JUDGE.instructions_units_max:,} units" in questions["description"]
    assert f"{FILE_JUDGE.choice_options_min}-{FILE_JUDGE.choice_options_max}" in questions["description"]
