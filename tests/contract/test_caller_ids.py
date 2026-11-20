"""Caller IDs keep their identity across allocation, provider requests, and tool results."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from tests.support.git_repo import repo_with_diff
from tests.support.jev import call_tool, choice

pytestmark = pytest.mark.anyio


@dataclass(frozen=True)
class IdInput:
    tool: str
    field: str
    prefix: str
    entry: dict[str, str]
    arguments: dict[str, Any]
    required: bool = False
    reserved: tuple[tuple[str, str], ...] = ()


_SEARCH = {"query": "find x"}
_TEXT = {"text": "x"}
_DESCRIPTION = {"description": "x"}
_VERIFY = {"claims": ["x is set"]}
_GATE = {**_VERIFY, "request": "Set x"}
_INPUTS = (
    IdInput("find", "candidates", "candidate", _TEXT, _SEARCH),
    IdInput("rerank", "candidates", "candidate", _TEXT, _SEARCH),
    IdInput("classify", "items", "item", _TEXT, {"classes": [_DESCRIPTION, _DESCRIPTION]}),
    IdInput("classify", "classes", "class", _DESCRIPTION, {"items": [_TEXT, _TEXT]}),
    IdInput("verify", "evidence", "evidence", _TEXT, _VERIFY, reserved=(("none_", "none"),)),
    IdInput(
        "gate",
        "evidence",
        "evidence",
        _TEXT,
        {**_GATE, "diff": "+x", "tests": "1 passed"},
        reserved=(("diff_", "diff"), ("tests_", "tests"), ("none_", "none")),
    ),
    IdInput(
        "gate",
        "evidence",
        "evidence",
        _TEXT,
        {**_GATE, "diff": [{"path": "x.py", "patch": "+x"}]},
        reserved=(("diff:x.py", "diff_x.py"), ("diff_", "diff")),
    ),
    IdInput(
        "gate_range",
        "evidence",
        "evidence",
        _TEXT,
        {**_GATE, "range": "HEAD~1..HEAD"},
        reserved=(("diff:x.py", "diff_x.py"), ("diff_", "diff")),
    ),
    IdInput(
        "decide",
        "candidates",
        "candidate",
        _DESCRIPTION,
        {"decision": "d", "evidence": "e", "priorities": "p"},
        required=True,
        reserved=(("ask_user_", "ask_user"), ("investigate", "investigate"), ("none", "none")),
    ),
    IdInput(
        "extract",
        "fields",
        "field",
        {"pattern": "x", **_DESCRIPTION},
        {"document": "x"},
        required=True,
    ),
)


def _cases() -> list[Any]:
    cases: list[Any] = []
    for item in _INPUTS:
        label = f"{item.tool}-{item.field}"
        if isinstance(item.arguments.get("diff"), list):
            label += "-files"
        for scenario, ids in (("duplicate", ("x", "x")), ("sanitized", ("x", "x_"))):
            cases.append(pytest.param(item, ids, "x", None, id=f"{label}-{scenario}"))
        for source, key in item.reserved:
            cases.append(pytest.param(item, (source, "x"), key, None, id=f"{label}-reserved-{key}"))
        if item.required:
            cases.append(pytest.param(item, (None, None), None, None, id=f"{label}-required"))
        else:
            stem = item.prefix
            for scenario, ids, expected in (
                ("missing-first", (None, f"{stem}0"), (f"{stem}2", f"{stem}0")),
                ("missing-last", (f"{stem}1", None), (f"{stem}1", f"{stem}2")),
                ("all-missing", (None, None), (f"{stem}0", f"{stem}1")),
            ):
                cases.append(pytest.param(item, ids, None, expected, id=f"{label}-{scenario}"))
    return cases


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(("item", "ids", "collision", "expected"), _cases())
async def test_caller_id_policy(
    item: IdInput,
    ids: tuple[str | None, str | None],
    collision: str | None,
    expected: tuple[str, str] | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if item.tool == "gate_range":
        monkeypatch.delenv("GIT_DIR", raising=False)
        monkeypatch.delenv("GIT_WORK_TREE", raising=False)
        monkeypatch.chdir(repo_with_diff(tmp_path))
    entries = [dict(item.entry) if raw is None else {**item.entry, "id": raw} for raw in ids]
    arguments = {**item.arguments, item.field: entries}
    answers: dict[str, Any] = {}
    if expected is not None:
        answers = {
            "best": choice(expected[0], *expected),
            "exists": {"noul": 1},
            "rel_0": {"noul": 1},
            "rel_1": {"noul": 0},
            "i0": choice("c0", "c0", "c1"),
            "i1": choice("c1", "c0", "c1"),
        }
    outcome = await call_tool(f"jev_{item.tool}", arguments, answers)

    if collision is not None:
        assert outcome.is_error, outcome.text
        assert outcome.code == "invalid_arguments"
        if (ids[0], collision) in item.reserved:
            assert outcome.text == f"ID {ids[0]!r} sanitizes to reserved ID {collision!r}."
        else:
            assert outcome.text == f"IDs {ids[0]!r} and {ids[1]!r} both sanitize to {collision!r}."
        assert outcome.requests == []
    elif expected is None:
        assert outcome.is_error and outcome.code == "invalid_arguments"
        assert f"Required at {item.field}[0].id" in outcome.text
        assert f"Required at {item.field}[1].id" in outcome.text
        assert outcome.requests == []
    else:
        assert not outcome.is_error, outcome.text
        assert outcome.requests
        assert "renamed_ids" not in outcome.payload
        if item.tool in ("find", "rerank"):
            rows = outcome.payload["top" if item.tool == "find" else "ranked"]
            assert [row["id"] for row in rows] == list(expected)
        elif item.tool == "classify":
            rows = outcome.payload["results"]
            key = "id" if item.field == "items" else "classification"
            assert [row[key] for row in rows] == list(expected)
        else:
            for state, _ in outcome.requests:
                evidence = cast(dict[str, Any], state)["evidence"]
                assert [row["id"] for row in evidence[:2]] == list(expected)
