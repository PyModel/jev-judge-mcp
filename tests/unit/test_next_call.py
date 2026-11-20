"""A held gate says who settles each row, and saying it never moves the action (ADR-0089)."""

import json
import re
from pathlib import Path
from typing import Any, cast, override

import pytest
from mcp.types import TextContent

from jev_judge_mcp.domain import JsonValue
from jev_judge_mcp.providers import Evaluation, ProviderError
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from tests.support.git_repo import commit_files, repo_with_diff
from tests.support.jev import NO_DEFECT, FakeProvider, call_tool, choice
from tests.support.next_call import assert_next_call

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


VERDICTS = ("verified", "unsupported", "contradicted")
KINDS = ("none", "wrong_result", "state_change", "error_handling", "safety_refusal")
SURE = {
    "correctness": {"score": 2, "confidence": 1},
    "spec_match": {"score": 2, "confidence": 1},
    "test_gap": {"score": 0, "confidence": 1},
    "blast_radius": {"score": 0, "confidence": 1},
    "safe_to_apply": {"noul": 1},
    "defect_kind": NO_DEFECT,
}
UNSURE = {**SURE, "correctness": {"score": 1.9, "confidence": 0.2}}
ARGS = {
    "request": "Double the argument",
    "diff": [{"path": "math.py", "patch": "@@ -1 +1 @@\n-return n\n+return n + 2"}],
    "claims": ["The tests passed"],
    "evidence": [{"id": "run", "kind": "tool_output", "text": "suite started\nFAIL test_double"}],
}
SOURCED = choice("run", "none", "run", "diff_math.py")
# A caller check names its own work; these words ask for another Jev call.
ASKS_FOR_A_CALL = re.compile(r"\b(Supply|Resubmit|resubmit|retry|recheck|Obtain|resend|Split|split)\b")
NAMES_INPUT = re.compile(r"\b(Supply|Split|split|git add -N|Review omitted)\b")

SCENARIOS: dict[str, tuple[dict[str, Any], dict[str, Any], str, str]] = {
    "accepted": (ARGS, {**SURE, "claim_0": choice("verified", *VERDICTS), "source_0": SOURCED}, "auto", "none"),
    "low-rubric-confidence": (ARGS, {**UNSURE, "claim_0": choice("verified", *VERDICTS)}, "review", "none"),
    "unsupported-claim": (ARGS, {**SURE, "claim_0": choice("unsupported", *VERDICTS)}, "review", "with_input"),
    "uncited-contradiction": (
        ARGS,
        {**SURE, "claim_0": choice("contradicted", *VERDICTS)},
        "review",
        "with_input",
    ),
    "cited-contradiction": (
        ARGS,
        {
            **SURE,
            "claim_0": choice("contradicted", *VERDICTS),
            "counterexample_0": choice("L3", "none", "L1", "L2", "L3"),
        },
        "escalate",
        "none",
    ),
    "cited-defect": (
        ARGS,
        {
            **SURE,
            "claim_0": choice("verified", *VERDICTS),
            "defect_kind": choice("wrong_result", *KINDS),
            "defect_line": choice("L2", "none", "L1", "L2"),
        },
        "escalate",
        "none",
    ),
    "invalid-answers": (ARGS, {}, "escalate", "none"),
    "diff-without-hunks": (
        {**ARGS, "diff": "I doubled the argument."},
        {**UNSURE, "claim_0": choice("verified", *VERDICTS)},
        "review",
        "with_input",
    ),
}


@pytest.mark.parametrize("name", SCENARIOS)
async def test_every_held_row_names_who_settles_it(name: str) -> None:
    args, answers, action, next_call = SCENARIOS[name]
    body = (await call_tool("jev_gate", args, answers)).payload
    assert (body["action"], body["rollup"]["action"], body["rollup"]["next_call"]) == (action, action, next_call)
    assert_next_call(body)
    for blocker in body.get("blockers", []):
        asks = ASKS_FOR_A_CALL.search(blocker["next_check"])
        if blocker["next_call"] == "none":
            assert asks is None, blocker
        else:
            assert NAMES_INPUT.search(blocker["next_check"]), blocker
    if next_call == "none":
        assert not any(ASKS_FOR_A_CALL.search(check) for check in body["next_checks"]), body["next_checks"]


async def test_a_repeated_unchanged_call_stays_settled_by_the_caller() -> None:
    """The loop the rule breaks: the same input returns the same judgment, so `none` stops the calls."""
    args, answers, _, _ = SCENARIOS["low-rubric-confidence"]
    first, second = [(await call_tool("jev_gate", args, answers)).payload for _ in range(2)]
    assert first == second and first["rollup"]["next_call"] == "none"


class _TooLargeFile(FakeProvider):
    """Refuses the file review of a patch containing `huge` as the provider's context overflow."""

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        if "huge" in str(cast(dict[str, JsonValue], state).get("diff", "")) and "claim_0" not in questions:
            self.requests.append((state, questions))
            error = ProviderError("context overflow")
            error.status, error.error_type = 400, "max_tokens_exceeded"
            raise error
        return await super()._send(state, questions, model, timeout)


async def test_a_file_the_provider_refused_asks_for_a_split_on_every_row() -> None:
    provider = _TooLargeFile({**SURE, "claim_0": choice("verified", *VERDICTS), "source_0": SOURCED})
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    args = {**ARGS, "diff": [{"path": "huge.py", "patch": "+ huge"}, {"path": "ok.py", "patch": "+ ok"}]}
    try:
        result = await toolset.call("jev_gate", args)
    finally:
        await toolset.aclose()
    body = json.loads(cast(TextContent, result.content[0]).text)
    rows = [blocker for blocker in body["blockers"] if blocker.get("path") == "huge.py"]
    assert body["action"] != "auto" and rows
    assert all(row["next_call"] == "with_input" and "Split huge.py by hunk" in row["next_check"] for row in rows)
    assert body["rollup"]["next_call"] == "with_input"
    assert_next_call(body)


class _ClaimsOverflow(FakeProvider):
    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        self.requests.append((state, questions))
        error = ProviderError("context overflow")
        error.status, error.error_type = 400, "max_tokens_exceeded"
        raise error


async def test_a_claim_overflow_with_no_held_file_still_asks_for_a_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The only change is a binary file (withheld, settled by the caller), so the split comes from the overflow."""
    for name in ("GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(name, raising=False)
    repo = repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    commit_files(repo, {"hidden.txt": "before\n", ".gitignore": ".gitattributes\n"})
    (repo / ".gitattributes").write_text("hidden.txt -diff\n")
    (repo / "hidden.txt").write_text("changed\n")
    for fixture in ("claims.json", "tests.log"):
        (repo / fixture).unlink()
    provider = _ClaimsOverflow({})
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    args = {"request": "r", "range": "HEAD", "claims": ["done"], "evidence": [{"id": "ci", "text": "1 passed"}]}
    try:
        result = await toolset.call("jev_gate_range", args)
    finally:
        await toolset.aclose()
    body = json.loads(cast(TextContent, result.content[0]).text)
    assert body["skipped"] == [{"path": "hidden.txt", "reason": "binary"}]
    assert {blocker["next_call"] for blocker in body["blockers"]} == {"none"}
    assert any(check.startswith("Provider context limit reached.") for check in body["next_checks"])
    assert body["rollup"]["next_call"] == "with_input"
    assert_next_call(body)
