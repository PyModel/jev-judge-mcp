"""jev_files_judge, the third extension tool beside the frozen ten (ADR-0077, divergence `file-judge-tools`).

The schema pinning, wire shapes, batch payload, zero-call paths, and the tools/list suffix are
owned here (the `test_file_judge.py` shape, which owns the shared question building and answer
projection); the expansion and prune mechanics are `tests/unit/test_files_judge.py`, and the
malformed-answer matrix stays `test_fail_closed.py`'s file_judge Case — the projection is that
tool's code, not a second copy.
"""

from typing import Any, cast

import pytest

from jev_judge_mcp.limits import FILE_JUDGE, FILES_JUDGE
from jev_judge_mcp.tools import TOOLS
from jev_judge_mcp.tools.files_judge import DEFINITION
from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio

NOTES = "tests/fixtures/file_judge/notes.txt"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def noul_args(paths: Any = None, criteria: Any = None) -> dict[str, Any]:
    return {
        "paths": [NOTES] if paths is None else paths,
        "kind": "noul",
        "instructions": "Does the state mention a port number?",
        "criteria": {"true": "a port is named", "false": "no port"} if criteria is None else criteria,
    }


async def test_one_answer_per_file_in_the_order_named_and_no_bytes_in_the_payload(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Input order, not path order: b is named before a, b answers first; dedup collapses `./a.txt`."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta", encoding="utf-8")
    outcome = await call_tool(
        "jev_files_judge",
        noul_args(["b.txt", "a.txt", "./a.txt"]),
        {"file": {"noul": 0.93}},
    )
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["tool"] == "jev_files_judge"
    assert payload["provider"] == "compatible"
    assert payload["results"] == [
        {"path": "b.txt", "answer": {"noul": 0.93}, "status": "ok"},
        {"path": "a.txt", "answer": {"noul": 0.93}, "status": "ok"},
    ]
    assert payload["calls"] == 2
    assert payload["usage"] == {"input_tokens": 2, "output_tokens": 2}
    assert "alpha" not in outcome.text and "beta" not in outcome.text  # the bytes stay out


async def test_the_state_is_the_file_and_one_question_serves_every_call() -> None:
    outcome = await call_tool("jev_files_judge", noul_args(), {"file": {"noul": 0.5}})
    assert not outcome.is_error
    assert len(outcome.requests) == 1
    state, questions = outcome.requests[0]
    request_state = cast(dict[str, object], state)
    assert request_state["path"] == NOTES
    assert "8080" in cast(str, request_state["content"])
    wire = cast(dict[str, object], questions["file"])
    assert wire["type"] == "noul"
    assert wire["instructions"] == "Does the state mention a port number?"
    assert wire["criteria"] == {"true": "a port is named", "false": "no port"}
    assert list(wire) == ["type", "instructions", "criteria"]


async def test_every_skip_is_reported_with_its_reason_and_costs_no_call(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "text.txt").write_text("plain text", encoding="utf-8")
    (tmp_path / "b.bin").write_bytes(b"ok\x00 then")
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    (tmp_path / "big.txt").write_text("a" * (FILES_JUDGE.file_units_max + 1), encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".env").write_text("TOKEN=deadbeef", encoding="utf-8")
    outcome = await call_tool(
        "jev_files_judge",
        noul_args(
            ["missing.md", "text.txt", "b.bin", "empty.txt", "big.txt", "sub", "node_modules", ".env", "../away"]
        ),
        {"file": {"noul": 0.9}},
    )
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["results"] == [{"path": "text.txt", "answer": {"noul": 0.9}, "status": "ok"}]
    assert payload["skipped"] == [
        {"path": "missing.md", "reason": "not_found"},
        {"path": "sub", "reason": "not_found"},
        {"path": "node_modules", "reason": "skipped_directory"},
        {"path": "../away", "reason": "outside_scope"},
        {"path": "b.bin", "reason": "binary"},
        {"path": "empty.txt", "reason": "empty"},
        {"path": "big.txt", "reason": "too_large"},
        {"path": ".env", "reason": "secret_file"},
    ]
    assert payload["calls"] == 1
    assert outcome.requests and len(outcome.requests) == 1


async def test_an_all_skip_batch_answers_with_zero_calls() -> None:
    outcome = await call_tool("jev_files_judge", noul_args(["missing.md"]), {"file": {"noul": 0.9}})
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["results"] == []
    assert payload["skipped"] == [{"path": "missing.md", "reason": "not_found"}]
    assert payload["calls"] == 0
    assert payload["usage"] is None
    assert payload["provider"] == "none"
    assert outcome.requests == []


async def test_criteria_broken_for_the_kind_refuses_before_any_io(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "text.txt").write_text("plain text", encoding="utf-8")
    outcomes = [
        await call_tool("jev_files_judge", noul_args(criteria=["a", "b"]), {"file": {"noul": 0.9}}),
        await call_tool(
            "jev_files_judge", noul_args(criteria={"small": "x"}) | {"kind": "score"}, {"file": {"noul": 0.9}}
        ),
        await call_tool("jev_files_judge", noul_args(paths=[]), {"file": {"noul": 0.9}}),
    ]
    for outcome in outcomes:
        assert outcome.is_error, outcome.text
        assert outcome.code == "invalid_arguments", outcome.text
        assert outcome.requests == []


async def test_an_unknown_kind_is_refused_at_the_argument_boundary() -> None:
    outcome = await call_tool("jev_files_judge", noul_args() | {"kind": "boolean"}, {"file": {"noul": 0.9}})
    assert outcome.is_error
    assert "kind must be one of noul, choice, score" in outcome.text
    assert outcome.requests == []


def test_the_schema_pins_the_adr_owned_caps_and_no_override() -> None:
    schema = DEFINITION.input_schema
    paths = schema["properties"]["paths"]
    assert paths["maxItems"] == FILES_JUDGE.patterns_max
    assert paths["minItems"] == 1
    assert paths["items"]["minLength"] == 1
    assert schema["properties"]["instructions"]["maxLength"] == FILE_JUDGE.instructions_units_max
    array = schema["properties"]["criteria"]["anyOf"][1]
    assert array["minItems"] == FILE_JUDGE.score_levels_min
    assert array["maxItems"] == FILE_JUDGE.score_levels_max
    assert array["items"]["maxLength"] == FILE_JUDGE.score_level_units_max
    assert schema["required"] == ["paths", "kind", "instructions", "criteria"]
    assert schema["additionalProperties"] is False
    assert "allow_outside_cwd" not in schema["properties"]
    assert schema["properties"]["recursive"]["type"] == "boolean"
    description = DEFINITION.description or ""
    assert "jev_find" in description  # picking among the answers is jev_find, not a new tool
    assert description.endswith(
        "Jev is invoked when an unresolved judgment earns a model decision. Deterministic evidence "
        "takes precedence; Jev is not a mandatory ceremony."
    )


def test_files_judge_is_published_after_jev_file_judge() -> None:
    names = tuple(tool.name for tool in TOOLS)
    assert names[names.index("jev_file_judge") + 2] == "jev_files_judge"
