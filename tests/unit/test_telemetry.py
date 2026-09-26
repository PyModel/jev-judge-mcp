"""P9 telemetry: span names and tree, metrics, the payload debug flag, and the tools-layer tracing seam."""

import ast
import inspect
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, get_args, override

import pytest

from jev_judge_mcp import validation
from jev_judge_mcp.domain import JsonValue
from jev_judge_mcp.providers import Evaluation, ProviderError
from jev_judge_mcp.server import configure_logging
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.telemetry import CAP_SCOPES, Span, Telemetry, describe, span
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from jev_judge_mcp.tools.observed import TRACED, validate_choice
from jev_judge_mcp.validation.caps import CapScope
from tests.security.tools import CASES, choice
from tests.support.jev import FakeProvider
from tests.support.secrets import secret_env
from tests.support.stdio import server_env

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parents[2]
CASE = {case.tool: case for case in CASES}
ALLOWED_ATTRIBUTE_TYPES = (bool, int, float, str)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    kept = server_env()
    for key in [key for key in os.environ if key not in kept]:
        monkeypatch.delenv(key)


class FailingProvider(FakeProvider):
    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        raise ProviderError("Fake 503: upstream down")


async def run(
    calls: list[tuple[str, Mapping[str, Any], Mapping[str, Any]]], provider: type[FakeProvider] = FakeProvider
) -> Telemetry:
    """Make `calls` (tool, arguments, answers) on one toolset; return its telemetry."""
    fake = provider({})
    toolset = Toolset(Runtime(load_settings(), provider_factory=lambda _: fake), TOOLS)
    try:
        for name, arguments, call_answers in calls:
            fake.answers = dict(call_answers)
            await toolset.call(name, arguments)
    finally:
        await toolset.aclose()
    return toolset.runtime.telemetry


async def test_a_gate_call_records_the_span_tree() -> None:
    telemetry = await run([("jev_gate", CASE["jev_gate"].arguments, CASE["jev_gate"].permissive)])
    spans = list(telemetry.spans.spans)
    root = spans[-1]
    assert root.name == "mcp.tool"
    assert root.parent is None
    assert root.attributes["tool"] == "jev_gate"
    assert root.attributes["outcome"] == "ok"
    assert root.attributes["action"] == "auto"
    assert {s.name for s in spans} == {"mcp.tool", "jev.evaluate", "jev.validate"}
    assert all(s.parent is root for s in spans[:-1])
    evaluate = next(s for s in spans if s.name == "jev.evaluate")
    assert evaluate.attributes == {"questions": 7, "provider": "compatible", "input_tokens": 1, "output_tokens": 1}
    for s in spans:
        assert s.duration >= 0
        assert all(isinstance(value, ALLOWED_ATTRIBUTE_TYPES) for value in s.attributes.values())
        assert s.payloads == {}


async def test_extract_records_regex_spans_and_timeouts() -> None:
    fields = [
        {"id": "build", "pattern": "[A-Z]{3}-\\d+", "description": "The build."},
        {"id": "slow", "pattern": "(a+)+$", "description": "Pathological."},
        {"id": "bad", "pattern": "(?<n>a)", "description": "Outside the subset."},
    ]
    arguments = {"document": "Build ABC-123 passed. " + "a" * 30 + "!", "fields": fields}
    answers = {"f0": choice("c0", {"c0": 0.98, "none_of_them": 0.02})}
    telemetry = await run([("jev_extract", arguments, answers)])
    regex = [s.attributes for s in telemetry.spans.spans if s.name == "regex.extract"]
    assert regex == [
        {"outcome": "ok", "candidates": 1, "too_long": 0, "candidates_truncated": False},
        {"outcome": "timeout"},
        {"outcome": "rejected"},
    ]
    metrics = telemetry.metrics.snapshot()
    assert metrics["regex_timeouts"] == 1
    assert metrics['actions{action="auto"}'] == 1
    assert metrics['duration_ms_count{span="regex.extract"}'] == 3


async def test_metrics_count_calls_errors_fail_closed_actions_tokens_and_truncation() -> None:
    malformed = {key: {"noul": float("nan")} for key in CASE["jev_review"].permissive}
    telemetry = await run(
        [
            ("jev_verify", CASE["jev_verify"].arguments, CASE["jev_verify"].permissive),
            ("jev_review", CASE["jev_review"].arguments, malformed),
            ("jev_review", {**CASE["jev_review"].arguments, "diff": "x" * 50_001}, CASE["jev_review"].permissive),
            ("jev_verify", {"claims": []}, {}),
            ("no_such_tool", {}, {}),
        ]
    )
    metrics = telemetry.metrics.snapshot()
    assert metrics['calls{tool="jev_verify"}'] == 2
    assert metrics['calls{tool="jev_review"}'] == 2
    assert metrics['calls{tool="unknown"}'] == 1
    assert metrics['outcomes{tool="jev_verify",outcome="arguments_error"}'] == 1
    assert metrics['outcomes{tool="unknown",outcome="unknown_tool"}'] == 1
    assert metrics['fail_closed{kind="score"}'] == 4
    assert metrics['actions{action="auto"}'] == 1
    assert metrics['actions{action="escalate"}'] == 1
    assert metrics['actions{action="review"}'] == 1
    assert metrics['truncated{scope="context"}'] == 1
    assert 'truncated{scope="item"}' not in metrics
    assert metrics['tokens{direction="input"}'] == 3
    assert metrics['duration_ms_count{span="mcp.tool"}'] == 5
    assert metrics['duration_ms_bucket{span="mcp.tool",le="+Inf"}'] == 5


AT_CANDIDATE_CAP = "x" * 2_000  # CANDIDATES.text_units, CLASSIFY.item_units and class_description_units
OVER_CANDIDATE_CAP = AT_CANDIDATE_CAP + "x"


@pytest.mark.parametrize(
    ("tool", "path"),
    [
        ("jev_find", ("candidates", 0, "text")),
        ("jev_rerank", ("candidates", 0, "text")),
        ("jev_classify", ("items", 0, "text")),
        ("jev_classify", ("classes", 1, "description")),
    ],
)
async def test_item_truncation_is_recorded_at_cap_plus_one_without_changing_the_answer(
    tool: str, path: tuple[str, int, str]
) -> None:
    case = CASE[tool]
    field, index, key = path
    entries = [dict(entry) for entry in case.arguments[field]]
    entries[index][key] = OVER_CANDIDATE_CAP
    cut = await run([(tool, {**case.arguments, field: entries}, case.permissive)])
    entries[index][key] = AT_CANDIDATE_CAP
    whole = await run([(tool, {**case.arguments, field: entries}, case.permissive)])
    assert cut.metrics.snapshot().get('truncated{scope="item"}') == 1
    assert not [name for name in cut.metrics.snapshot() if name.startswith('truncated{scope="context"')]
    assert not [name for name in whole.metrics.snapshot() if name.startswith("truncated")]
    actions = {name: value for name, value in cut.metrics.snapshot().items() if name.startswith("actions")}
    assert actions == {name: value for name, value in whole.metrics.snapshot().items() if name.startswith("actions")}


async def test_inputs_the_schema_bounds_are_never_counted_as_cut() -> None:
    """jev_compare's passages and jev_extract's document go through the ledger, but the schema rejects
    anything over their cap first, so a call at the cap records no cut."""
    compare = {**CASE["jev_compare"].arguments, "passage_a": "x" * 20_000}
    extract = {**CASE["jev_extract"].arguments, "document": CASE["jev_extract"].arguments["document"].ljust(50_000)}
    telemetry = await run(
        [
            ("jev_compare", compare, CASE["jev_compare"].permissive),
            ("jev_extract", extract, CASE["jev_extract"].permissive),
        ]
    )
    metrics = telemetry.metrics.snapshot()
    assert metrics['outcomes{tool="jev_compare",outcome="ok"}'] == 1
    assert metrics['outcomes{tool="jev_extract",outcome="ok"}'] == 1
    assert not [name for name in metrics if name.startswith("truncated")]


HEADLINES: dict[str, tuple[str | None, str | None]] = {
    "jev_verify": ("auto", "review"),
    "jev_screen": (None, None),  # pass and block are not Actions; only screen's review is
    "jev_find": (None, None),
    "jev_classify": ("auto", "review"),
    "jev_decide": (None, None),
    "jev_rerank": (None, None),
    "jev_compare": ("auto", "review"),
    "jev_extract": ("auto", "review"),
    "jev_review": ("auto", "escalate"),
    "jev_gate": ("auto", "escalate"),
}
"""(permissive, hostile) headline Action per tool, `None` for a call that returns none."""


def headline_counts(telemetry: Telemetry) -> dict[str, float]:
    metrics = telemetry.metrics.snapshot()
    return {action: metrics[f'actions{{action="{action}"}}'] for action in ("auto", "review", "escalate")}


@pytest.mark.parametrize("answers", ["permissive", "hostile"])
@pytest.mark.parametrize("tool", list(HEADLINES))
async def test_each_call_counts_one_headline_action(tool: str, answers: str) -> None:
    case = CASE[tool]
    telemetry = await run([(tool, case.arguments, case.permissive if answers == "permissive" else case.hostile)])
    expected = HEADLINES[tool][answers == "hostile"]
    assert headline_counts(telemetry) == {action: int(action == expected) for action in ("auto", "review", "escalate")}
    root = telemetry.spans.spans[-1]
    assert root.attributes.get("action") == expected


async def test_screen_review_is_its_headline_action() -> None:
    case = CASE["jev_screen"]
    answers = {**case.permissive, "injection": {"noul": 0.5}}
    telemetry = await run([("jev_screen", case.arguments, answers), ("jev_screen", case.arguments, {})])
    assert headline_counts(telemetry) == {"auto": 0, "review": 2, "escalate": 0}


async def test_per_item_actions_are_counted_apart_from_the_headline() -> None:
    arguments = {"claims": ["a", "b", "c"], "evidence": "e"}
    answers = {
        "relation_claim0": choice("supports", {"supports": 0.98, "contradicts": 0.01, "says_nothing": 0.01}),
        "relation_claim1": choice("supports", {"supports": 0.98, "contradicts": 0.01, "says_nothing": 0.01}),
    }
    telemetry = await run([("jev_verify", arguments, answers)])
    metrics = telemetry.metrics.snapshot()
    assert headline_counts(telemetry) == {"auto": 0, "review": 1, "escalate": 0}
    assert metrics['item_actions{tool="jev_verify",action="auto"}'] == 2
    assert metrics['item_actions{tool="jev_verify",action="review"}'] == 1


async def test_extract_with_no_action_row_has_no_headline() -> None:
    arguments = {"document": "nothing here", "fields": [{"id": "x", "pattern": "XYZ-\\d+", "description": "d"}]}
    telemetry = await run([("jev_extract", arguments, {})])
    assert headline_counts(telemetry) == {"auto": 0, "review": 0, "escalate": 0}
    assert "action" not in telemetry.spans.spans[-1].attributes


async def test_provider_errors_are_counted_by_class_never_by_message() -> None:
    telemetry = await run(
        [("jev_verify", CASE["jev_verify"].arguments, CASE["jev_verify"].permissive)], provider=FailingProvider
    )
    metrics = telemetry.metrics.snapshot()
    assert metrics['provider_errors{error="ProviderError"}'] == 1
    assert metrics['outcomes{tool="jev_verify",outcome="provider_error"}'] == 1
    evaluate = next(s for s in telemetry.spans.spans if s.name == "jev.evaluate")
    assert "upstream" not in describe(evaluate)


SECRET_CASE = CASE["jev_extract"]
CANDIDATE = "ABC-124"


def leaked(telemetry: Telemetry, secret: str) -> dict[str, bool]:
    span_text = "\n".join(describe(s) for s in telemetry.spans.spans)
    metric_text = "\n".join(telemetry.metrics.snapshot())
    return {
        "spans": secret in span_text or CANDIDATE in span_text,
        "metrics": secret in metric_text or CANDIDATE in metric_text,
    }


def pasted_secret_call(secret: str) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    """An extract call whose document carries a configured secret and whose answer picks `CANDIDATE`."""
    arguments = {**SECRET_CASE.arguments, "document": f"{SECRET_CASE.arguments['document']} key={secret}"}
    return ("jev_extract", arguments, SECRET_CASE.permissive)


@pytest.mark.parametrize("flag", [None, "0", "false"])
async def test_payload_text_is_absent_without_the_debug_flag(monkeypatch: pytest.MonkeyPatch, flag: str | None) -> None:
    env = secret_env()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if flag is not None:
        monkeypatch.setenv("JEV_MCP_TELEMETRY_PAYLOADS", flag)
    secret = env["TYPESAFE_API_KEY"]
    telemetry = await run([pasted_secret_call(secret), ("no_such_tool", {secret: CANDIDATE}, {})])
    assert all(s.payloads == {} for s in telemetry.spans.spans)
    assert leaked(telemetry, secret) == {"spans": False, "metrics": False}


async def test_payload_text_is_present_only_under_the_debug_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    env = secret_env()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("JEV_MCP_TELEMETRY_PAYLOADS", "1")
    secret = env["TYPESAFE_API_KEY"]
    telemetry = await run([pasted_secret_call(secret)])
    root = telemetry.spans.spans[-1]
    assert secret in root.payloads["arguments"]
    assert CANDIDATE in root.payloads["result"]
    regex = next(s for s in telemetry.spans.spans if s.name == "regex.extract")
    assert regex.payloads == {"pattern": SECRET_CASE.arguments["fields"][0]["pattern"]}
    # Payload text lives in `payloads` only; attributes and metrics never carry it.
    assert leaked(telemetry, secret) == {"spans": True, "metrics": False}
    assert all(secret not in str(s.attributes) and CANDIDATE not in str(s.attributes) for s in telemetry.spans.spans)


async def test_debug_span_logs_are_redacted_of_configured_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = secret_env()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("JEV_MCP_TELEMETRY_PAYLOADS", "1")
    secret = env["TYPESAFE_API_KEY"]
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    configure_logging("DEBUG", load_settings().secret_values())
    try:
        await run([pasted_secret_call(secret)])
    finally:
        root.handlers[:], root.level = saved
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "span mcp.tool" in captured.err
    assert "span regex.extract" in captured.err
    assert "metrics {" in captured.err
    assert secret not in captured.err


def test_outside_a_tool_call_nothing_is_recorded() -> None:
    with span("regex.extract", outcome="ok") as detached:
        assert validate_choice(None, ["a"]) is None
    assert detached.parent is None


async def test_nested_spans_restore_the_parent() -> None:
    telemetry = Telemetry()
    with telemetry.span("mcp.tool", tool="t") as root:
        with span("regex.extract", outcome="ok") as first:
            pass
        with span("regex.extract", outcome="timeout") as second:
            pass
    assert first.parent is root
    assert second.parent is root
    assert [s.attributes.get("outcome") for s in telemetry.spans.spans] == ["ok", "timeout", None]


def test_escaping_errors_are_named_not_quoted() -> None:
    telemetry = Telemetry()
    with pytest.raises(ValueError, match="secret text"), telemetry.span("jev.validate", kind="choice"):
        raise ValueError("secret text")
    recorded: Span = telemetry.spans.spans[-1]
    assert recorded.attributes == {"kind": "choice", "error": "ValueError"}


def test_every_validation_callable_is_traced() -> None:
    functions = {
        name
        for name in validation.__all__
        if inspect.isfunction(getattr(validation, name)) and name.startswith("validate_")
    }
    assert functions == set(TRACED)


def test_tools_import_traced_callables_only_from_observed() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "src/jev_judge_mcp/tools").glob("*.py")):
        if path.name == "observed.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.module.startswith(("jev_judge_mcp.policy", "jev_judge_mcp.validation")):
                offenders += [f"{path.name}:{alias.name}" for alias in node.names if alias.name in TRACED]
    assert offenders == []


def test_cap_scope_labels_are_the_ledger_scopes() -> None:
    assert CAP_SCOPES == get_args(CapScope.__value__)


def test_the_metrics_snapshot_is_logged_at_info_periodically(caplog: pytest.LogCaptureFixture) -> None:
    """A long-lived server surfaces its counters at INFO before shutdown, not only at DEBUG in aclose."""
    telemetry = Telemetry(metrics_interval=2)
    with caplog.at_level(logging.INFO, logger="jev_judge_mcp.telemetry"):
        for _ in range(2):
            with telemetry.span("mcp.tool", tool="jev_verify"):
                pass
        with telemetry.span("jev.evaluate", questions=1):  # not a tool span: does not advance the count
            pass
    snapshots = [record for record in caplog.records if record.message.startswith("metrics ")]
    assert len(snapshots) == 1
    assert snapshots[0].levelname == "INFO"
    assert 'calls{tool="jev_verify"}' in snapshots[0].message
