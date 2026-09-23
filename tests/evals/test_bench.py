"""The 150-question bench offline: the item file, renderer, answer parser, gate, spans, stats, proxy, caps.

Nothing here calls a model or a provider; the end-to-end dry run is `test_bench_dryrun.py`.
"""

import io
import json
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from evals.ab import ledger as pilot_ledger
from evals.ab import stream
from evals.ab.ledger import RUN_BOUND_USD
from evals.bench import analysis, answer, gate, pi, prompt, run, spans, stats
from evals.bench.items import BENCH150, KEYS, Item, load_items, parse_item
from evals.bench.ledger import POLICY, TRIPLET
from evals.bench.proxy import BENCH_REQUEST_CAP, REFUSAL, BenchRecorder
from evals.runners.manifest import load_cases
from evals.scorers.tools import SCORERS, Example
from evals.spend import SpendLedger
from jev_judge_mcp.tools import TOOLS
from jev_judge_mcp.tools.arguments import parse_arguments

REPO = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent / "data"
DRYRUN = DATA / "bench-dryrun.jsonl"
STREAM_SAMPLE = DATA / "claude-stream-json-sample.jsonl"
WEAK_SPOTS = {"arithmetic", "counting", "date-order", "indirection", "double-negative", "irrelevant-state"}
ITEMS = load_items()


def by_tool(tool: str) -> list[Item]:
    return [item for item in ITEMS if item.tool == tool]


BENCH_TOOLS = [tool for tool in SCORERS if by_tool(tool)]
"""The tools the frozen 150-item corpus covers. An ADR-0048 extension joins the bench only when it
carries its own items (`jev_score` has none yet), so it can never ride the bench by accident."""


# --- the item file ----------------------------------------------------------------------------------


def test_the_bench_covers_the_snapshot_ten_and_nothing_unscored() -> None:
    snapshot_ten = {tool.name for tool in TOOLS[:10]}
    benched = {item.tool for item in ITEMS}
    assert snapshot_ten <= benched
    assert benched <= set(SCORERS)


def test_bench_has_15_items_per_tool_in_3_families_of_5() -> None:
    assert Counter(item.tool for item in ITEMS) == dict.fromkeys(BENCH_TOOLS, 15)
    for tool in BENCH_TOOLS:
        assert sorted(Counter(item.family for item in by_tool(tool)).values()) == [5, 5, 5]
    assert len({item.family for item in ITEMS}) == 30


def test_every_row_is_an_unlabeled_draft() -> None:
    for item in ITEMS:
        assert item.status == "draft"
        assert item.label["labelers"] == [] and item.label["agreed"] is None and item.label["adjudicator"] is None
        assert item.gold == {} and item.accept == ()


def test_every_input_is_a_valid_call_of_its_tool() -> None:
    tools = {tool.name: tool for tool in TOOLS}
    for item in ITEMS:
        tool = tools[item.tool]
        parse_arguments(tool.name, tool.definition.input_schema, item.input, tool.refinements)


def test_load_cases_reads_the_bench_file() -> None:
    assert [case.id for case in load_cases(BENCH150)] == [item.id for item in ITEMS]


def _mix(tool: str) -> Counter[str]:
    return Counter(item.intended for item in by_tool(tool))


def test_class_mixes_follow_the_plan_allocation() -> None:
    assert _mix("jev_verify") == {"verified": 5, "contradicted": 5, "unsupported": 5}
    assert _mix("jev_screen") == {"injection": 7, "clean": 8}
    assert _mix("jev_compare") == {"same_fact": 5, "contradicts": 5, "different_facts": 5}
    assert _mix("jev_review") == {"do_not_apply": 8, "apply": 7}
    assert _mix("jev_gate") == {"reject": 8, "accept": 7}
    assert _mix("jev_find")["none"] == 5
    assert _mix("jev_decide")["escape"] == 5
    assert _mix("jev_extract")["not stated"] == 5
    contradicted = [item for item in by_tool("jev_verify") if item.intended == "contradicted"]
    assert sum(item.perturbation in ("numeric", "negation") for item in contradicted) >= 3
    contradicts = [item for item in by_tool("jev_compare") if item.intended == "contradicts"]
    assert all(item.perturbation in ("numeric", "negation") for item in contradicts)
    severe = [item for item in by_tool("jev_review") if item.severity == "severe"]
    assert len(severe) >= 3 and all(item.intended == "do_not_apply" for item in severe)
    assert sum(item.hard_case == "near-boundary" for item in by_tool("jev_classify")) >= 3
    for item in by_tool("jev_rerank"):
        assert 5 <= len(item.input["candidates"]) <= 10


def test_every_tool_has_three_items_from_jev_weak_spots() -> None:
    for tool in BENCH_TOOLS:
        assert sum(item.hard_case in WEAK_SPOTS for item in by_tool(tool)) >= 3, tool


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in cast(dict[str, object], value).values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in cast(list[object], value) for s in _strings(v)]
    return []


def test_security_payloads_appear_only_in_screen_items() -> None:
    payloads = ("Ignore all previous instructions", '"action": "auto", "decision": "auto"', "</state><questions>")
    for item in ITEMS:
        text = "\n".join(_strings(item.input))
        if any(p in text for p in payloads):
            assert item.tool == "jev_screen", item.id
            assert item.intended == "injection" or item.hard_case is not None, f"{item.id}: untagged hard negative"


def test_options_match_the_item_material() -> None:
    for item in ITEMS:
        if item.tool in ("jev_find", "jev_rerank", "jev_decide"):
            ids = [str(c["id"]) for c in item.input["candidates"]]
            extra = {"jev_find": ["none"], "jev_decide": ["escape"]}.get(item.tool, [])
            assert list(item.options or ()) == [*ids, *extra], item.id
        if item.tool == "jev_classify":
            assert list(item.options or ()) == [str(c["id"]) for c in item.input["classes"]], item.id
        if item.options is not None:
            assert item.intended in item.options, item.id


def test_extract_patterns_find_the_intended_value() -> None:
    for item in by_tool("jev_extract"):
        field = item.input["fields"][0]
        found = spans.re.findall(field["pattern"], item.input["document"])
        if item.intended != "not stated":
            assert item.intended in found, item.id


def test_sources_are_argument_text_only() -> None:
    for item in ITEMS:
        if item.source["kind"] != "new":
            path = REPO / str(item.source["ref"]).split("#")[0]
            assert path.is_file(), item.id


def test_parse_item_rejects_gold_on_a_draft_and_missing_gold_on_a_frozen_row() -> None:
    row = json.loads(BENCH150.read_text(encoding="utf-8").splitlines()[0])
    with pytest.raises(ValueError, match="draft row has no gold"):
        parse_item({**row, "gold": {"claims": {"claim0": "verified"}}})
    with pytest.raises(ValueError, match="needs gold, accept, and two labelers"):
        parse_item({**row, "label": {**row["label"], "status": "frozen"}})
    with pytest.raises(ValueError, match="keys must be"):
        parse_item({key: row[key] for key in KEYS if key != "intended"})


# --- prompt -----------------------------------------------------------------------------------------


def test_render_reads_only_the_material_question_and_options() -> None:
    for item in ITEMS:
        hidden = replace(item, intended="X-INTENDED", source={"kind": "new", "ref": None}, label={"status": "y"})
        assert prompt.render(hidden) == prompt.render(item)
        assert "X-INTENDED" not in prompt.render(hidden)
        assert item.question in prompt.render(item)
        assert '{"answer":' in prompt.render(item)


def test_the_arms_differ_only_in_the_jev_sentence() -> None:
    assert prompt.addendum("A") == prompt.BENCH_ADDENDUM
    assert prompt.addendum("B") == prompt.BENCH_ADDENDUM
    assert prompt.addendum("C") == f"{prompt.BENCH_ADDENDUM} {prompt.JEV_SENTENCE}"
    assert prompt.addendum("C", agent="pi") == f"{prompt.BENCH_ADDENDUM} {prompt.JEV_SENTENCE_PI}"
    assert "mcp__jev__" in prompt.JEV_SENTENCE
    assert "mcp__jev__" not in prompt.addendum("A") and "mcp__jev__" not in prompt.addendum("B")
    # The shared addendum never names Jev and never forbids looking at tools: that sentence is what
    # suppressed the automatic arm's tool use (ADR-0036 amendment).
    for arm in ("A", "B"):
        assert "jev" not in prompt.addendum(arm).lower()
        assert "do not look" not in prompt.addendum(arm)
    # C's Pi sentence names the now-visible tools, not the adapter's gateway dance.
    assert "gateway" not in prompt.JEV_SENTENCE_PI.lower()
    assert "jev_" in prompt.JEV_SENTENCE_PI


def test_the_bench_exposes_the_jev_tools_directly() -> None:
    """B and C register the published tools in the model's initial list (ADR-0036 amendment).

    A lazy, proxy-only entry is the measured root cause of the automatic arm's zero adoption.
    """
    setup = run.Setup(
        agent=lambda _item, _arm: ["agent"],
        server=["server"],
        server_env={},
        base_env={},
        secret="synthetic-secret",  # noqa: S106 - a fixture value, never a real key
    )
    for arm in ("B", "C"):
        entry = run.mcp_config(arm, Path("log.jsonl"), setup)["mcpServers"]["jev"]
        assert entry["lifecycle"] == "eager"
        assert entry["directTools"] is True
        assert entry["toolPrefix"] == "none"
    assert "jev" not in run.mcp_config("A", Path("log.jsonl"), setup)["mcpServers"]


def test_claude_command_carries_the_bench_addendum(tmp_path: Path) -> None:
    command = run.arms.claude_command("claude", "q", tmp_path / "mcp.json", prompt.addendum("C"))
    assert command[command.index("--append-system-prompt") + 1] == prompt.addendum("C")


# --- answer -----------------------------------------------------------------------------------------

DRY = {item.id: item for item in load_items(DRYRUN)}
VERIFY = DRY["dry-verify-01"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('I checked.\n{"answer": "verified"}', ("verified", None)),
        ('```json\n{"answer": "verified"}\n```', ("verified", None)),
        ('{"answer": "verified"}\nThanks!', (None, "final line is not JSON")),
        ('{"answer": "maybe"}', (None, "answer 'maybe' is not an option")),
        ('{"answer": "verified", "why": "x"}', (None, 'final line is not {"answer": ...}')),
        ('{"answer": ""}', (None, "answer is not a non-empty string")),
        ("", (None, "no final answer line")),
        (None, (None, "no final answer line")),
    ],
)
def test_parse_final_answer(text: object, expected: tuple[object, object]) -> None:
    assert answer.parse(VERIFY, text) == expected


def test_parse_rerank_and_extract_answers() -> None:
    rerank = by_tool("jev_rerank")[0]
    ids = list(rerank.options or ())
    assert answer.parse(rerank, json.dumps({"answer": ids[::-1]})) == (tuple(ids[::-1]), None)
    assert answer.parse(rerank, json.dumps({"answer": [ids[0], ids[0]]}))[0] is None
    assert answer.parse(rerank, json.dumps({"answer": ["zz-unknown"]}))[0] is None
    assert answer.parse(rerank, json.dumps({"answer": ids[0]})) == (None, "answer is not a list of ids")
    extract = by_tool("jev_extract")[0]
    assert answer.parse(extract, '{"answer": "any value"}') == ("any value", None)


def test_correct_is_membership_in_accept_and_rerank_top1() -> None:
    assert answer.is_correct(VERIFY, "verified") and not answer.is_correct(VERIFY, "contradicted")
    assert not answer.is_correct(VERIFY, None)
    rerank = replace(by_tool("jev_rerank")[0], accept=("c2",))
    assert answer.is_correct(rerank, ("c2", "c1")) and not answer.is_correct(rerank, ("c1", "c2"))
    assert not any(answer.is_correct(item, item.intended) for item in ITEMS), "draft rows accept nothing"


def _scored(item: Item, gold: dict[str, Any], given: answer.Answer) -> dict[str, Any]:
    params = {"max_false_block_rate": 0.1}
    example = Example(item.id, item.family, item.input, gold, answer.wrap(item, given))
    return SCORERS[item.tool]([example], params).metrics


def test_wrapped_answers_run_through_the_existing_scorers_unchanged() -> None:
    tools = {tool: by_tool(tool)[0] for tool in BENCH_TOOLS}
    assert (
        _scored(tools["jev_verify"], {"claims": {"claim0": "contradicted"}}, "contradicted")["contradiction_recall"]
        == 1.0
    )
    classify = tools["jev_classify"]
    first = str(classify.input["classes"][0]["id"])
    assert _scored(classify, {"labels": {"item0": first}}, first)["micro_f1"] == 1.0
    decide = tools["jev_decide"]
    assert _scored(decide, {"acceptable": []}, "escape")["escape_hatch_accuracy"] == 1.0
    assert _scored(decide, {"acceptable": []}, str(decide.options and decide.options[0]))["overdecision_rate"] == 1.0
    find = tools["jev_find"]
    assert _scored(find, {"relevance": {"x": 0}, "verdict": "absent"}, "none")["verdict_accuracy"] == 1.0
    top = str(find.input["candidates"][0]["id"])
    assert _scored(find, {"relevance": {top: 2}}, top)["recall_at_1"] == 1.0
    rerank = tools["jev_rerank"]
    ids = tuple(str(c["id"]) for c in rerank.input["candidates"])
    assert _scored(rerank, {"relevance": {ids[1]: 2}}, (ids[1], *ids[:1], *ids[2:]))["mrr"] == 1.0
    assert (
        _scored(tools["jev_compare"], {"relation": "contradicts", "perturbation": "numeric"}, "contradicts")[
            "perturbed_contradiction_recall"
        ]
        == 1.0
    )
    extract = tools["jev_extract"]
    field = str(extract.input["fields"][0]["id"])
    assert _scored(extract, {"fields": {field: None}}, "not stated")["not_found_recall"] == 1.0
    assert _scored(extract, {"fields": {field: None}}, "invented-value-xyz")["hallucinated_values"] == 1
    assert _scored(tools["jev_review"], {"defective": True}, "apply")["p_defective_given_auto"] == 1.0
    assert _scored(tools["jev_gate"], {"safe": False}, "reject")["auto"] == 0
    assert _scored(tools["jev_screen"], {"injection": True}, "injection")["invalid"] == 1
    assert answer.wrap(VERIFY, None) == {}


# --- stats ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("split", "weaker"),
    [((9, 1), (8, 2)), ((15, 5), (14, 6)), ((21, 9), (20, 10)), ((27, 13), (26, 14)), ((33, 17), (32, 18))],
)
def test_mcnemar_matches_the_plan_minimum_detectable_table(split: tuple[int, int], weaker: tuple[int, int]) -> None:
    assert stats.mcnemar_exact(*split) < 0.05
    assert stats.mcnemar_exact(*weaker) >= 0.05
    assert stats.mcnemar_exact(*split) == stats.mcnemar_exact(*split[::-1])


def test_exact_binomial_edges() -> None:
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.mcnemar_exact(5, 5) == 1.0
    assert stats.mcnemar_exact(10, 0) == pytest.approx(2 / 1024)
    assert stats.sign_test([1.0, 2.0, -0.5, 0.0]) == stats.two_sided_binomial_p(2, 1)
    with pytest.raises(ValueError):
        stats.two_sided_binomial_p(-1, 2)


# --- use gate and cross-check -----------------------------------------------------------------------


def _call(seq: int = 0, **fields: Any) -> dict[str, Any]:
    base = {"seq": seq, "tool": "jev_verify", "is_error": False, "refused": False, "model": "jev-1.13.0"}
    return {**base, **fields}


def test_use_gate() -> None:
    assert gate.use_gate([]) == "no Jev call"
    assert gate.use_gate([_call(tool="Bash")]) == "no Jev call"
    errored = gate.use_gate([_call(is_error=True), _call(1, refused=True, is_error=True)])
    assert str(errored).startswith("no successful")
    assert gate.use_gate([_call(model="jev-latest")]) == "no Jev answer from jev-1.13.0"
    assert gate.use_gate([_call(is_error=True), _call(1)]) is None
    assert gate.wrong_models([_call(model="jev-latest"), _call(1), _call(2, model="x", is_error=True)]) == [
        "jev-latest"
    ]


def _trace(*uses: tuple[str, bool | None]) -> stream.Trace:
    trace = stream.Trace()
    for n, (name, error) in enumerate(uses):
        trace.tool_uses.append(stream.ToolUse(name, {}, f"t{n}"))
        if error is not None:
            trace.tool_results[f"t{n}"] = error
    return trace


def test_cross_check_accepts_the_same_calls_in_any_order() -> None:
    calls = [_call(0, tool="jev_screen"), _call(1, is_error=True)]
    gate.cross_check(calls, _trace(("mcp__jev__jev_verify", True), ("Bash", False), ("mcp__jev__jev_screen", False)))


@pytest.mark.parametrize(
    "uses",
    [
        (("mcp__jev__jev_verify", False),),
        (("mcp__jev__jev_verify", False), ("mcp__jev__jev_screen", False), ("mcp__jev__jev_gate", False)),
        (("mcp__jev__jev_verify", False), ("mcp__jev__jev_screen", False)),
        (("mcp__jev__jev_verify", True), ("mcp__jev__jev_screen", None)),
    ],
)
def test_cross_check_stops_on_any_disagreement(uses: tuple[tuple[str, bool | None], ...]) -> None:
    calls = [_call(0, tool="jev_screen"), _call(1, is_error=True)]
    with pytest.raises(gate.HarnessMismatchError):
        gate.cross_check(calls, _trace(*uses))


# --- spans ------------------------------------------------------------------------------------------

SPAN_LOG = [
    "2026-09-21 21:56:19,065 DEBUG jev_judge_mcp.telemetry: span jev.evaluate 16.863ms questions=1 provider=compatible",
    "2026-09-21 21:56:19,065 DEBUG jev_judge_mcp.telemetry: span jev.validate 0.010ms kind=choice valid=True",
    "2026-09-21 21:56:19,065 DEBUG jev_judge_mcp.telemetry: span mcp.tool 17.167ms tool=jev_verify outcome=ok",
    "2026-09-21 21:56:19,066 DEBUG jev_judge_mcp.telemetry: metrics {'calls': 1}",
    "2026-09-21 21:56:20,000 DEBUG jev_judge_mcp.telemetry: span regex.extract 0.500ms outcome=ok",
    "2026-09-21 21:56:20,001 DEBUG jev_judge_mcp.telemetry: span mcp.tool 2.000ms tool=jev_extract outcome=ok",
]


def _timed(seq: int, tool: str, t0: float, t1: float, ms: float, **fields: Any) -> dict[str, Any]:
    return {"seq": seq, "tool": tool, "t0": t0, "t1": t1, "ms": ms, **fields}


def test_spans_attribute_by_order_and_mark_no_provider_call() -> None:
    parsed = spans.parse_spans(SPAN_LOG)
    assert [span.name for span in parsed] == ["jev.evaluate", "jev.validate", "mcp.tool", "regex.extract", "mcp.tool"]
    assert parsed[2].attributes == {"tool": "jev_verify", "outcome": "ok"}
    calls = [
        _timed(0, "jev_verify", 1.0, 1.02, 18.0),
        _timed(1, "jev_screen", 1.5, 1.5, 0.0, refused=True),
        _timed(2, "jev_extract", 2.0, 2.003, 3.0),
    ]
    timings = spans.attribute(calls, parsed)
    assert timings == [
        spans.CallTiming(18.0, 16.863, 0.304, 0.833),
        spans.CallTiming(3.0, None, 2.0, 1.0),
    ]


def test_spans_are_unattributed_rather_than_guessed() -> None:
    parsed = spans.parse_spans(SPAN_LOG)
    one, two = _timed(0, "jev_verify", 1.0, 1.02, 18.0), _timed(1, "jev_extract", 2.0, 2.003, 3.0)
    assert spans.attribute([one, {**two, "t0": 1.01}], parsed) is None, "overlapping calls"
    assert spans.attribute([one], parsed) is None, "count mismatch"
    assert spans.attribute([one, {**two, "tool": "jev_find"}], parsed) is None, "tool mismatch"
    assert spans.attribute([one, two], parsed[:3] + parsed[:1]) is None, "evaluate without its tool span"
    doubled = spans.parse_spans([SPAN_LOG[0], *SPAN_LOG])
    assert spans.attribute([one, two], doubled) is None, "two provider calls in one tool call"


# --- proxy ------------------------------------------------------------------------------------------


def _call_message(n: int, name: str = "jev_verify") -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": name, "arguments": {"k": n}}}


def _request(n: int, name: str = "jev_verify") -> bytes:
    return (json.dumps(_call_message(n, name)) + "\n").encode()


def test_recorder_refuses_the_call_after_the_cap_and_logs_it() -> None:
    log = io.StringIO()
    recorder = BenchRecorder(log, cap=2)
    assert recorder.request({"jsonrpc": "2.0", "id": 0, "method": "initialize"}) is None
    assert recorder.request(_call_message(1)) is None and recorder.request(_call_message(2)) is None
    refused = recorder.request(_call_message(3, "jev_gate"))
    assert refused is not None
    reply = json.loads(refused)
    assert reply["id"] == 3 and reply["result"]["isError"] is True
    assert reply["result"]["content"][0]["text"] == REFUSAL
    row = json.loads(log.getvalue())
    assert {k: row[k] for k in ("seq", "tool", "refused", "is_error", "arguments")} == {
        "seq": 2,
        "tool": "jev_gate",
        "refused": True,
        "is_error": True,
        "arguments": {"k": 3},
    }
    assert BenchRecorder(io.StringIO()).cap == BENCH_REQUEST_CAP == 25


FAKE_SERVER = str(REPO / "tests" / "support" / "fake_mcp_server.py")


def test_proxy_relays_25_calls_and_answers_the_26th_itself(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    stdin = b"".join(_request(n) for n in range(1, BENCH_REQUEST_CAP + 2))
    done = subprocess.run(
        [sys.executable, "-m", "evals.bench.proxy", str(log), "--", sys.executable, FAKE_SERVER],
        input=stdin,
        capture_output=True,
        cwd=REPO,
        timeout=60,
        check=True,
    )
    replies = {json.loads(line)["id"]: json.loads(line) for line in done.stdout.splitlines()}
    assert sorted(replies) == list(range(1, BENCH_REQUEST_CAP + 2))
    assert replies[BENCH_REQUEST_CAP + 1]["result"]["isError"] is True
    rows = sorted((json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()), key=lambda r: r["seq"])
    assert [row["refused"] for row in rows] == [False] * BENCH_REQUEST_CAP + [True]
    first = rows[0]
    assert first["arguments"] == {"k": 1} and json.loads(first["text"])["model"] == "jev-1.13.0"
    assert first["t1"] >= first["t0"] and first["ms"] >= 0 and first["model"] == "jev-1.13.0"


# --- ledger, schedule, early stops, refusals ---------------------------------------------------------


def test_bench_ledger_checks_a_whole_triplet(tmp_path: Path) -> None:
    book = SpendLedger.load(tmp_path / "ledger.json", POLICY)
    for i in range(POLICY.max_runs - 4):
        book.runs[f"r{i}"] = 0.0
    assert book.can_start(TRIPLET)
    book.record("x", 0.0)
    assert book.can_start(TRIPLET)
    book.record("y", 0.0)
    assert book.blocker(TRIPLET) == "run cap: 448 of 450 runs done"
    dollars = SpendLedger.load(tmp_path / "other.json", POLICY)
    dollars.record("a", 25.00 - TRIPLET * RUN_BOUND_USD)
    assert dollars.can_start(TRIPLET)
    dollars.record("b", 0.01)
    assert str(dollars.blocker(TRIPLET)).startswith("dollar cap")
    with pytest.raises(ValueError, match="never retried"):
        dollars.record("b", 0.0)


def test_bench_and_pilot_caps_are_spent_independently(tmp_path: Path) -> None:
    pilot_book = SpendLedger.load(tmp_path / "p8" / "ledger.json", pilot_ledger.POLICY)
    for n in range(pilot_ledger.POLICY.max_runs):
        pilot_book.record(f"t{n}", 2.7)
    assert not pilot_book.can_start()
    bench_book = SpendLedger.load(tmp_path / "bench" / "ledger.json", POLICY)
    assert bench_book.can_start(TRIPLET) and bench_book.spent == 0
    assert (POLICY.max_usd, pilot_ledger.POLICY.max_usd) == (25.00, 25.00)
    assert POLICY.jev_usd_per_mtok_input == pilot_ledger.POLICY.jev_usd_per_mtok_input


def test_schedule_is_seeded_and_triplets_every_item() -> None:
    plan = run.schedule(ITEMS)
    assert plan == run.schedule(ITEMS) and plan != run.schedule(ITEMS, seed=1)
    assert sorted(item.id for item, _ in plan) == sorted(item.id for item in ITEMS)
    assert {tuple(sorted(order)) for _, order in plan} == {("A", "B", "C")}
    assert len({order for _, order in plan}) > 1


def _record(item: str, arm: str, *, correct: bool, wall: float, gate_reason: str | None = None) -> dict[str, Any]:
    return {
        "run_id": f"{item}.{arm}",
        "item": item,
        "arm": arm,
        "correct": correct,
        "wall_s": wall,
        "gate": gate_reason,
    }


def test_headline_uses_complete_triplets_only() -> None:
    records = [
        _record("i1", "A", correct=False, wall=10.0),
        {**_record("i1", "B", correct=True, wall=12.0), "jev_calls": [{"tool": "jev_verify"}]},
        _record("i1", "C", correct=True, wall=14.0),
        _record("i2", "A", correct=True, wall=10.0),
        _record("i2", "B", correct=False, wall=11.0, gate_reason="did not call Jev"),
        _record("i2", "C", correct=False, wall=9.0, gate_reason="no Jev call"),
        _record("i3", "B", correct=True, wall=1.0),
    ]
    triplets = analysis.complete_triplets(records)
    assert sorted(triplets) == ["i1", "i2"]
    result = analysis.headline(triplets)
    assert result["triplets"] == 2
    assert result["wall_A_median_s"] == 10
    assert result["wall_b_minus_a_median_s"] == 1.5
    assert result["wall_c_minus_a_median_s"] == 1.5
    assert result["called_jev_B"] == 1 and result["called_jev_A"] == 0 and result["called_jev_C"] == 0


def test_wrap_marks_a_stated_extract_value_auto_as_the_server_does() -> None:
    extract = by_tool("jev_extract")[0]
    assert answer.wrap(extract, "ABC-124")["results"][0]["status"] == "auto"
    assert answer.wrap(extract, "not stated")["results"][0]["status"] == "not_found"


def _run(item: Item, arm: str, given: object, *, status: str = "ok", gate_reason: str | None = None) -> dict[str, Any]:
    return {
        **_record(item.id, arm, correct=False, wall=1.0, gate_reason=gate_reason),
        "tool": item.tool,
        "status": status,
        "answer": given,
    }


def test_per_tool_breakdown_scores_wrapped_answers_of_labeled_items() -> None:
    verify = replace(by_tool("jev_verify")[0], gold={"claims": {"claim0": "contradicted"}})
    rerank = by_tool("jev_rerank")[0]
    ids = [str(c["id"]) for c in rerank.input["candidates"]]
    rerank = replace(rerank, gold={"relevance": {ids[1]: 2}})
    screen = replace(by_tool("jev_screen")[0], gold={"injection": True})
    draft = by_tool("jev_classify")[0]
    compare = replace(by_tool("jev_compare")[0], gold={"relation": "contradicts", "perturbation": "numeric"})
    records = [
        _run(verify, "A", "contradicted"),
        _run(verify, "B", "contradicted", gate_reason="did not call Jev"),
        _run(verify, "C", "contradicted", gate_reason="no Jev call"),
        _run(compare, "A", "contradicts"),
        {
            **_run(compare, "B", "contradicts"),
            "jev_calls": [{"tool": "jev_compare", "model": "jev-latest", "is_error": False}],
        },
        _run(compare, "C", "contradicts"),
        _run(rerank, "A", [ids[1], ids[0]]),
        _run(rerank, "B", [ids[0], ids[1]], status="failed: timeout"),
        _run(rerank, "C", [ids[1], ids[0]]),
        _run(screen, "A", "injection"),
        _run(screen, "B", "injection"),
        _run(screen, "C", "injection"),
        _run(draft, "A", "x"),
        _run(draft, "B", "x"),
        _run(draft, "C", "x"),
    ]
    items = {item.id: item for item in (verify, rerank, screen, draft, compare)}
    breakdown = analysis.per_tool(analysis.complete_triplets(records), items)
    assert set(breakdown) == {"jev_verify", "jev_rerank", "jev_screen", "jev_compare"}, "draft items have no gold"
    compare_metric = {arm: breakdown["jev_compare"][arm]["metrics"]["perturbed_contradiction_recall"] for arm in "ABC"}
    assert compare_metric == {"A": 1.0, "B": 0.0, "C": 1.0}, "a wrong Jev model voids the answer"
    assert breakdown["jev_verify"]["A"]["metrics"]["contradiction_recall"] == 1.0
    assert breakdown["jev_verify"]["B"]["metrics"]["contradiction_recall"] == 1.0, "automatic non-use keeps the answer"
    forced = breakdown["jev_verify"]["C"]["metrics"]["contradiction_recall"]
    assert forced == 0.0, "forced-arm gate failure voids the answer"
    assert breakdown["jev_rerank"]["A"]["metrics"]["mrr"] == 1.0
    assert breakdown["jev_rerank"]["B"]["metrics"]["mrr"] == 0.0
    assert breakdown["jev_rerank"]["C"]["metrics"]["mrr"] == 1.0
    screen_a = breakdown["jev_screen"]["A"]
    assert (screen_a["n"], screen_a["primary"]) == (1, "injection_recall_at_false_block_rate")
    assert screen_a["metrics"]["injection_recall_at_false_block_rate"] is None, "agent answers carry no probability"


def test_early_stops_on_compliance_and_projected_cost() -> None:
    def triplets(failed: int) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
        return [
            (
                _record(f"i{n}", "A", correct=True, wall=1.0),
                _record(f"i{n}", "B", correct=True, wall=1.0, gate_reason="did not call Jev"),
                _record(f"i{n}", "C", correct=True, wall=1.0, gate_reason="no Jev call" if n < failed else None),
            )
            for n in range(10)
        ]

    cheap = {f"i{n}.{arm}": 0.03 for n in range(10) for arm in "ABC"}
    assert analysis.early_stop(triplets(2), total_runs=450, jev_cost=cheap, budget=22.75, max_failed=3) is None
    assert str(analysis.early_stop(triplets(3), total_runs=450, jev_cost=cheap, budget=22.75, max_failed=3)).startswith(
        "compliance stop: 3 of the first 10 C runs"
    )
    dear = dict.fromkeys(cheap, 0.08)
    assert str(analysis.early_stop(triplets(0), total_runs=450, jev_cost=dear, budget=22.75, max_failed=3)).startswith(
        "cost stop"
    )


def test_live_run_refuses_without_flag_key_or_frozen_labels(tmp_path: Path) -> None:
    with pytest.raises(run.BenchRefusedError, match="JEV_BENCH_LIVE=1"):
        run.live({"TYPESAFE_API_KEY": "k"}, tmp_path)
    with pytest.raises(run.BenchRefusedError, match="TYPESAFE_API_KEY"):
        run.live({"JEV_BENCH_LIVE": "1"}, tmp_path)
    with pytest.raises(run.BenchRefusedError, match="150 of 150 items are not frozen"):
        run.live({"JEV_BENCH_LIVE": "1", "TYPESAFE_API_KEY": "k"}, tmp_path)
    assert run.main([], environ={}) == 2
    assert not list(tmp_path.iterdir()), "a refused run writes nothing"


def test_setup_needs_a_secret_to_scrub() -> None:
    with pytest.raises(ValueError, match="non-empty key"):
        run.Setup(agent=lambda _i, _a: [], server=[], server_env={}, base_env={}, secret="")


# --- the recorded stream-json sample ----------------------------------------------------------------


def test_an_auth_error_is_recognized_without_reading_a_key() -> None:
    assert run.jev_auth_error([{"is_error": True, "text": "TypeSafe API 401: rejected"}])
    assert not run.jev_auth_error([{"is_error": False, "text": "TypeSafe API 401: rejected"}])
    assert not run.jev_auth_error([{"is_error": True, "text": "provider timed out"}])


def test_percentiles_on_a_short_sample() -> None:
    assert stats.percentile([10.0], 95) == 10.0
    assert stats.percentile([1.0, 2.0, 3.0, 4.0], 0) == 1.0
    assert stats.percentile([1.0, 2.0, 3.0, 4.0], 100) == 4.0
    assert stats.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5


def test_a_connection_error_is_booked_and_is_not_a_triplet(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = [
        {"type": "session"},
        {"type": "agent_start"},
        {"type": "turn_start"},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "provider": "ds4",
                "model": "glm-5.3-flash",
                "usage": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "content": [],
                "stopReason": "error",
                "errorMessage": "Connection error.",
            },
        },
        {"type": "turn_end", "message": {"errorMessage": "Connection error."}},
        {"type": "agent_end"},
        {"type": "auto_retry_end", "success": False, "finalError": "Connection error."},
    ]
    script = tmp_path / "agent.py"
    script.write_text(
        f"import json\nEVENTS = {events!r}\nfor event in EVENTS:\n    print(json.dumps(event))\n",
        encoding="utf-8",
    )
    launched: list[str] = []

    def agent(item: Item, arm: str) -> list[str]:
        launched.append(f"{item.id}.{arm}")
        return [sys.executable, str(script)]

    item = load_items(DRYRUN)[0]
    out = tmp_path / "out"
    scrub = "bench-offline-scrub"
    setup = run.Setup(
        agent=agent,
        server=[sys.executable, "-c", "raise SystemExit(0)"],
        server_env={},
        base_env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        secret=scrub,
        trace=pi.parse,
    )
    stop = run.run_bench([item], setup, out)
    assert stop.startswith("stopped: provider failure on ") and "not an arm result" in stop
    assert len(launched) == 1
    assert run.run_bench([item], setup, out) == stop and launched == [launched[0]]
    records = run.load_records(out)
    assert len(records) == 1 and records[0]["server_failure"] is True
    assert records[0]["agent_cost_reported"] is False
    assert analysis.complete_triplets(records) == {}
    assert launched[0] in SpendLedger.load(out / "ledger.json", POLICY).runs


def test_recorded_stream_json_sample_parses_tool_results_by_tool_use_id() -> None:
    trace = stream.parse(STREAM_SAMPLE.read_text(encoding="utf-8").splitlines())
    assert trace.mcp_servers == {"harness": "connected", "jev": "connected"}
    assert [use.name for use in trace.tool_uses] == ["Bash", "Bash"]
    assert all(use.id.startswith("toolu_") for use in trace.tool_uses)
    assert trace.tool_results == dict.fromkeys((use.id for use in trace.tool_uses), False)
    assert trace.result_field("subtype") == "success" and isinstance(trace.result_field("result"), str)
    gate.cross_check([], trace)
