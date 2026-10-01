"""jev_file_judge, the second extension tool beside the frozen ten (ADR-0077, divergence `file-judge-tools`).

The schema pinning, wire shapes, typed zero-call refusals, and the no-content payload are owned
here (the `test_score_tool.py` shape); the malformed-answer matrix is `test_fail_closed.py`'s
Case, and the filesystem mechanics (sniff, caps, scope) are `tests/unit/test_file_state.py`.
"""

from typing import Any, cast

import pytest

from jev_judge_mcp.limits import FILE_JUDGE
from jev_judge_mcp.tools import TOOLS
from jev_judge_mcp.tools.file_judge import DEFINITION
from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio

NOTES = "tests/fixtures/file_judge/notes.txt"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def noul_args(path: str = NOTES, criteria: Any = None) -> dict[str, Any]:
    return {
        "path": path,
        "kind": "noul",
        "instructions": "Does the state mention a port number?",
        "criteria": {"true": "a port is named", "false": "no port"} if criteria is None else criteria,
    }


async def test_happy_path_projects_the_typed_answer_and_never_the_bytes() -> None:
    outcome = await call_tool("jev_file_judge", noul_args(), {"file": {"noul": 0.93}})
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["tool"] == "jev_file_judge"
    assert payload["provider"] == "compatible"
    assert payload["path"] == NOTES
    assert payload["kind"] == "noul"
    assert payload["answer"] == {"noul": 0.93}
    assert payload["status"] == "ok"
    assert payload["usage"] == {"input_tokens": 1, "output_tokens": 1}
    assert "8080" not in outcome.text  # the file's bytes stay out of the payload


async def test_the_state_is_the_file_and_the_question_is_written_against_content() -> None:
    outcome = await call_tool("jev_file_judge", noul_args(), {"file": {"noul": 0.5}})
    assert not outcome.is_error
    state, questions = outcome.requests[0]
    request_state = cast(dict[str, object], state)
    assert request_state["path"] == NOTES
    assert "8080" in cast(str, request_state["content"])
    assert list(questions) == ["file"]
    wire = cast(dict[str, object], questions["file"])
    assert wire["type"] == "noul"
    assert wire["instructions"] == "Does the state mention a port number?"
    assert wire["criteria"] == {"true": "a port is named", "false": "no port"}
    assert list(wire) == ["type", "instructions", "criteria"]


async def test_a_choice_question_carries_the_options_and_keys_the_answer_to_them() -> None:
    args = noul_args(criteria={"config": "hard-coded values", "env": "read from the environment", "mixed": "both"})
    args["kind"] = "choice"
    args["instructions"] = "Where does the port come from?"
    outcome = await call_tool(
        "jev_file_judge",
        args,
        {"file": {"choice": "config", "probabilities": {"config": 0.9, "env": 0.05, "mixed": 0.05}, "confidence": 0.7}},
    )
    assert not outcome.is_error, outcome.text
    _, questions = outcome.requests[0]
    wire = cast(dict[str, object], questions["file"])
    assert wire["type"] == "choice"
    assert wire["criteria"] == {"config": "hard-coded values", "env": "read from the environment", "mixed": "both"}
    assert outcome.payload["answer"] == {
        "choice": "config",
        "probabilities": {"config": 0.9, "env": 0.05, "mixed": 0.05},
        "confidence": 0.7,
    }


async def test_a_score_question_carries_the_ordered_levels_and_the_full_distribution() -> None:
    args = noul_args(criteria=["plain", "mixed", "expert"])
    args["kind"] = "score"
    args["instructions"] = "How technical is the content?"
    outcome = await call_tool(
        "jev_file_judge",
        args,
        {"file": {"score": 1.5, "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1}, "confidence": 0.8}},
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["answer"] == {
        "score": 1.5,
        "nearest_level": 1,
        "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1},
        "confidence": 0.8,
    }
    assert outcome.payload["status"] == "ok"


async def test_a_malformed_answer_fails_closed_but_keeps_the_frame() -> None:
    outcome = await call_tool("jev_file_judge", noul_args(), {"file": {"noul": 1.5}})
    assert not outcome.is_error
    payload = outcome.payload
    assert payload["status"] == "invalid_response"
    assert payload["answer"] == {"noul": None}
    assert payload["usage"] == {"input_tokens": 1, "output_tokens": 1}


async def test_every_refusal_is_typed_and_makes_no_provider_call(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "text.txt").write_text("plain text", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"ok\x00 then garbage")
    (tmp_path / "big.txt").write_text("a" * (FILE_JUDGE.file_units_max + 1), encoding="utf-8")
    (tmp_path / "dir").mkdir()
    cases = [
        (noul_args("missing.txt"), "not_found"),
        (noul_args("dir"), "not_a_file"),
        (noul_args("binary.bin"), "binary_file"),
        (noul_args("big.txt"), "file_too_large"),
        (noul_args("../outside.txt"), "path_outside_scope"),
    ]
    for args, code in cases:
        outcome = await call_tool("jev_file_judge", args, {"file": {"noul": 0.9}})
        assert outcome.is_error, (code, outcome.text)
        assert outcome.code == code, (code, outcome.text)
        assert outcome.requests == [], code


async def test_criteria_broken_for_the_kind_refuses_before_any_call(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "text.txt").write_text("plain text", encoding="utf-8")
    outcomes = [
        await call_tool("jev_file_judge", noul_args(criteria=["a", "b"]), {"file": {"noul": 0.9}}),
        await call_tool(
            "jev_file_judge", noul_args(criteria={"small": "x"}) | {"kind": "score"}, {"file": {"noul": 0.9}}
        ),
        await call_tool(
            "jev_file_judge", noul_args(criteria={"only": "x"}) | {"kind": "choice"}, {"file": {"noul": 0.9}}
        ),
        await call_tool(
            "jev_file_judge", noul_args(criteria={"a": "x", "b": 3}) | {"kind": "choice"}, {"file": {"noul": 0.9}}
        ),
        await call_tool("jev_file_judge", noul_args(criteria={"maybe": "x", "false": "y"}), {"file": {"noul": 0.9}}),
        await call_tool(
            "jev_file_judge",
            noul_args(criteria={"a": "x", "b": "y" * (FILE_JUDGE.choice_option_units_max + 1)}) | {"kind": "choice"},
            {"file": {"noul": 0.9}},
        ),
    ]
    for outcome in outcomes:
        assert outcome.is_error, outcome.text
        assert outcome.code == "invalid_arguments", outcome.text
        assert outcome.requests == []


async def test_an_unknown_kind_is_refused_at_the_argument_boundary() -> None:
    outcome = await call_tool("jev_file_judge", noul_args() | {"kind": "boolean"}, {"file": {"noul": 0.9}})
    assert outcome.is_error
    assert "kind must be one of noul, choice, score" in outcome.text
    assert outcome.requests == []


def test_the_schema_carries_the_adr_owned_caps_and_no_override() -> None:
    schema = DEFINITION.input_schema
    assert schema["properties"]["instructions"]["maxLength"] == FILE_JUDGE.instructions_units_max
    assert schema["properties"]["path"]["minLength"] == 1
    array = schema["properties"]["criteria"]["anyOf"][1]
    assert array["minItems"] == FILE_JUDGE.score_levels_min
    assert array["maxItems"] == FILE_JUDGE.score_levels_max
    assert array["items"]["minLength"] == 1
    assert array["items"]["maxLength"] == FILE_JUDGE.score_level_units_max
    assert schema["required"] == ["path", "kind", "instructions", "criteria"]
    assert schema["additionalProperties"] is False
    assert "allow_outside_cwd" not in schema["properties"]


def test_file_judge_is_published_after_jev_score() -> None:
    names = tuple(tool.name for tool in TOOLS)
    assert names[-1] == "jev_file_judge"
    assert names[-2] == "jev_score"
