---
status: accepted
---

# Added tests do not fail a correct L4 run

The 2026-09-27 L4 study (Pi, `opencode-go/deepseek-v4.1-flash`, thinking high) graded 4 of 9 with-Jev runs as failures only because the agent added test methods to a pre-existing test file after a correct fix. Hidden acceptance was 39/39 on both arms, and the stated decision matched gold 9/9 on both arms. The grader's byte-identical rule on `protected_files()` could not tell an added test from a weakened one.

This is an eval-harness decision. It does not change an MCP tool, a schema, a threshold, or the frozen TS 0.5.0 reference, so it is not a divergence and it is not entered in `docs/reference/divergences.json`.

## Decision

- A run is correct when the hidden acceptance tests all pass, no pre-existing test regresses, every pre-existing test is unchanged in content, every added test passes and is relevant, and the stated decision matches gold when the task has gold.
- Adding a test function to a pre-existing test file, or adding a new test file, is allowed. Modifying, deleting, skipping, or weakening any pre-existing test function, or any pre-existing non-test statement those tests sit beside, still fails the run.
- Added tests are executed in the agent's own suite and must both pass and remain relevant: a failing added test fails the run (`added test failing`), and so does an irrelevant one (`irrelevant added test`). Relevant means the test's file imports the task's `target_module` — deterministic, never a model's opinion. Each added test is recorded with its file, name, outcome, and relevance.
- `protected_changed` keeps its meaning: pre-existing test files whose bytes differ, or that were deleted. It no longer decides success. `old_rule_success` is the pre-ADR-0073 verdict, so a study can show both counts.
- Neither arm's prompt changes, and the with-Jev addendum still does not mention tests.
- The paired task set grows past the original three, which stay as they are. New tasks cover a done-claim, screening fetched text that carries an injection, docs-versus-code, finding a line, extracting a value, reviewing a patch, and two controls whose answer the code already states. Every gold option is the one the hidden acceptance tests accept.

## Consequences

- A correct fix plus `test_day_30_after_delivery_is_on_time` added to `tests/test_refunds.py` grades as correct. The same tree fails `old_rule_success`.
- `success` on records written after this ADR includes the decision match. Records written before it keep the old grader's bit; the report says so.

## Amendment 2026-11-19: isolate code under test from trusted assertions

The trusted collector runs the hidden acceptance and pristine snapshot tests. Imports of task
modules resolve to proxies. Each proxy loads and calls its module in a separate isolated
Python interpreter rooted in a disposable copy of the agent tree. The collector is
non-dumpable on Linux where supported. The worker sends replies over a dedicated pipe.

The tagged JSON codec carries plain scalars, lists, string-keyed dictionaries, tuples, dates,
datetimes, and decimals. Builtin exceptions cross by exact type identity and message. Other
agent exceptions become `AgentError`. An unsupported value, missed deadline, crash, or invalid
reply fails the affected test, even when an assertion catches the transport error. Tests
whose module cannot import fail by comparison against the reference solution's passing IDs.

Agent-added tests execute against the agent's own tree as before. The protected-file and
pre-existing-test edit rules still apply.
- The run cap is the grid: eleven tasks, two arms, three repeats. The dollar cap stays $25.
