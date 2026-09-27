# D3 paired comparison, 2026-09-27

Pi `opencode-go/deepseek-v4.1-flash`, thinking high, 3 reps per task per arm. The model override and the $3.00 cap were invocation-only; the tree's Pi model pin and the $25 study cap are unchanged. `make ci` passed before this run.

Stop: all 33 pairs recorded. Ledger spent $1.0886 of $3.00.

All 66 recorded runs: new-rule success 61/66, old-rule success 49/66, saves (old fails, new passes) 13. With-Jev arm called Jev in 3/33 runs; a pair whose with-Jev arm did not call Jev is excluded from the measured-pair outcomes below and is still in the all-runs rule table.

Definitions of unnecessary calls, a Jev-changed decision, relevance, and failure category are in the Definitions section.

# Agent outcome study: does Jev improve a coding agent while it uses the Jev MCP tools?

Same agent, same task, same repository revision, with and without the Jev MCP server; each task hinges on a judgment a Jev tool is for, and the with-Jev arm is told to use that tool. Raw per-run records: `evals/reports/agent-outcomes/` (gitignored).

History, not this study: `p8-pilot.md` (2026-09-21; no run called a Jev tool) and `bench150.md` (accuracy unscored, item rate is evaluator overhead) measured something else and are kept as recorded.

## pi

- **agent:** 0.87.1
- **Jev revision:** `28e940ff85020099ab3a88f9d5b239f277bf2f90`; **fixture sha256:** `160850290dcdfb2928350f62466d1bc8df8df2198fc82c417e11706881dd0ea3`
- **hardware:** macOS-27.0-arm64-arm-64bit, arm64, 18 CPUs
- **timeout:** 900s wall per run
- **harness server (both arms):** `request_human_review`
- **system addendum (both arms):** You are working in a small Python repository. Complete the task in the user's message, run the test suite to check your work, and stop when you are done. Use whichever of your available tools help.
- **Jev (arm B only):** `python -m jev_judge_mcp`, `JEV_PROVIDER=typesafe`, `JEV_MCP_MODEL=jev-1.13.0`
- **grader:** ADR-0071: hidden acceptance tests, no regressions, pre-existing test content unchanged, added tests must pass, decision matches gold (`evals/ab/grade.py`)
- **model:** `opencode-go/deepseek-v4.1-flash`, thinking `high`
- **temperature:** Pi's default for the model, identical in both arms
- **tools:** Pi's built-in tools + `pi-mcp-adapter` (loaded in both arms)
- **limits:** no turn or token cap exposed by `pi --print`; the timeout bounds the run

Runs recorded: 66. Pairs: 33. Measured pairs: 3.

Excluded pairs (not evidence of any Jev effect):

- 30 x B: Jev not used: no Jev call

### Outcomes over 3 measured pairs

| measure | A: without Jev | B: with Jev MCP |
|---|---|---|
| tasks solved | 3/3 | 3/3 |
| time to a correct solution, s | median 23.9 (n=3, min 7.9, max 28.5) [7.9, 23.9, 28.5] | median 258.1 (n=3, min 100.4, max 444.5) [100.4, 258.1, 444.5] |
| correct solutions per hour | 178.89 | 13.45 |
| final tests passed | 3/3 | 3/3 |
| wrong branches (runs with one / runs on tasks with signatures; total) | 0/2; 0 | 0/2; 0 |
| test cycles per run | median 1.0 (n=3, min 1.0, max 2.0) [1.0, 1.0, 2.0] | median 2.0 (n=3, min 2.0, max 3.0) [2.0, 2.0, 3.0] |
| retries (total) | 1 | 4 |
| tool calls per solved task | 8.0 | 47.3 |
| tokens per solved task (context + output) | 28909 | 908706 |
| Jev calls (total; runs with one) | 0; 0/3 | 4; 3/3 |
| unnecessary Jev calls (total) | 0 | 1 |
| Jev round trip, ms | not recorded | median 325.6 (n=4, min 237.4, max 702.0) [237.4, 318.8, 332.4, 702.0] |
| old-rule successes | 3/3 | 3/3 |
| failure categories | none | none |
| judge accuracy (decision = gold) | 3/3 | 3/3 |

### Paired

- Pairs: 3. Solved by both: 3; only without Jev: 0; only with Jev: 0; neither: 0.
- Wall time, with minus without, pairs both solved (s): median 229.6 (n=3, min 76.5, max 436.5) [76.5, 229.6, 436.5].
- Jev changed the decision: 0 observed changes; 0 of those equal gold; 3 pairs had no Jev option to compare.
- Old-rule vs new-rule successes are the arm rows `old-rule successes` and `tasks solved`.
- Descriptive only: no significance test, and n is small.

### Per task

| task | arm | new-rule success | old-rule success | Jev calls | unnecessary | failure categories |
|---|---|---|---|---|---|---|
| j10-control-spec | A | 1/1 | 1/1 | 0 | 0 | none |
| j10-control-spec | B | 1/1 | 1/1 | 1 | 1 | none |
| j6-docs-vs-code | A | 1/1 | 1/1 | 0 | 0 | none |
| j6-docs-vs-code | B | 1/1 | 1/1 | 2 | 0 | none |
| j7-find-line | A | 1/1 | 1/1 | 0 | 0 | none |
| j7-find-line | B | 1/1 | 1/1 | 1 | 0 | none |

### Old rule vs new rule, all recorded runs

Not limited to measured pairs. New-rule success allows added tests and requires the decision to match gold. Old-rule success fails any byte change to a pre-existing test file and does not read the decision. A save is a run the old rule fails and the new rule passes.

| task | arm | runs | new-rule | old-rule | saves |
|---|---|---|---|---|---|
| j1-refund-window | A | 3 | 3/3 | 2/3 | 1 |
| j1-refund-window | B | 3 | 3/3 | 0/3 | 3 |
| j10-control-spec | A | 3 | 3/3 | 3/3 | 0 |
| j10-control-spec | B | 3 | 2/3 | 3/3 | 0 |
| j11-control-label | A | 3 | 3/3 | 3/3 | 0 |
| j11-control-label | B | 3 | 3/3 | 3/3 | 0 |
| j2-ticket-route | A | 3 | 1/3 | 1/3 | 0 |
| j2-ticket-route | B | 3 | 2/3 | 1/3 | 1 |
| j3-installment-patch | A | 3 | 3/3 | 3/3 | 0 |
| j3-installment-patch | B | 3 | 3/3 | 3/3 | 0 |
| j4-done-claim | A | 3 | 3/3 | 3/3 | 0 |
| j4-done-claim | B | 3 | 3/3 | 0/3 | 3 |
| j5-screen-injection | A | 3 | 3/3 | 2/3 | 1 |
| j5-screen-injection | B | 3 | 3/3 | 3/3 | 0 |
| j6-docs-vs-code | A | 3 | 3/3 | 3/3 | 0 |
| j6-docs-vs-code | B | 3 | 2/3 | 2/3 | 0 |
| j7-find-line | A | 3 | 3/3 | 3/3 | 0 |
| j7-find-line | B | 3 | 3/3 | 3/3 | 0 |
| j8-extract-rate | A | 3 | 3/3 | 3/3 | 0 |
| j8-extract-rate | B | 3 | 3/3 | 3/3 | 0 |
| j9-review-patch | A | 3 | 3/3 | 1/3 | 2 |
| j9-review-patch | B | 3 | 3/3 | 1/3 | 2 |

All recorded runs: new-rule 61/66, old-rule 49/66, saves 13.

### Runs

| run | measurement | success | tests passed | wall s | tokens | tool calls | Jev calls | test cycles | wrong branches | decision | failure |
|---|---|---|---|---|---|---|---|---|---|---|---|
| j1-refund-window.A.r1 | measured | yes | yes | 27.0 | 49355 | 14 | 0 | 3 | 0 | delivery-date-inclusive |  |
| j1-refund-window.A.r2 | measured | yes | yes | 8.8 | 24474 | 4 | 0 | 1 | 0 | delivery-date-inclusive |  |
| j1-refund-window.A.r3 | measured | yes | yes | 18.8 | 28773 | 11 | 0 | 1 | 0 | delivery-date-inclusive |  |
| j1-refund-window.B.r1 | Jev not used: no Jev call | yes | yes | 151.6 | 475417 | 40 | 0 | 5 | 0 | delivery-date-inclusive |  |
| j1-refund-window.B.r2 | Jev not used: no Jev call | yes | yes | 90.9 | 376258 | 35 | 0 | 1 | 0 | delivery-date-inclusive |  |
| j1-refund-window.B.r3 | Jev not used: no Jev call | yes | yes | 62.4 | 379782 | 32 | 0 | 2 | 0 | delivery-date-inclusive |  |
| j10-control-spec.A.r1 | measured | yes | yes | 10.8 | 14845 | 5 | 0 | 1 | 0 | use-spec |  |
| j10-control-spec.A.r2 | measured | yes | yes | 7.9 | 10434 | 3 | 0 | 1 | 0 | use-spec |  |
| j10-control-spec.A.r3 | measured | yes | yes | 10.4 | 14836 | 4 | 0 | 1 | 0 | use-spec |  |
| j10-control-spec.B.r1 | Jev not used: no Jev call | yes | yes | 44.7 | 166390 | 24 | 0 | 2 | 0 | use-spec |  |
| j10-control-spec.B.r2 | measured | yes | yes | 444.5 | 1563968 | 58 | 1 | 2 | 0 | use-spec |  |
| j10-control-spec.B.r3 | Jev not used: no Jev call | no | yes | 900.0 | 239865 | 26 | 0 | 0 | 0 | none | timeout |
| j11-control-label.A.r1 | measured | yes | yes | 7.8 | 21998 | 5 | 0 | 1 | 0 | use-required |  |
| j11-control-label.A.r2 | measured | yes | yes | 8.8 | 14651 | 4 | 0 | 1 | 0 | use-required |  |
| j11-control-label.A.r3 | measured | yes | yes | 13.0 | 19775 | 4 | 0 | 2 | 0 | use-required |  |
| j11-control-label.B.r1 | Jev not used: no Jev call | yes | yes | 131.7 | 343951 | 29 | 0 | 2 | 0 | use-required |  |
| j11-control-label.B.r2 | Jev not used: no Jev call | yes | yes | 75.2 | 361034 | 31 | 0 | 2 | 0 | use-required |  |
| j11-control-label.B.r3 | Jev not used: no Jev call | yes | yes | 207.1 | 261242 | 29 | 0 | 1 | 0 | use-required |  |
| j2-ticket-route.A.r1 | measured | no | no | 29.2 | 39834 | 15 | 0 | 1 | n/a | security | acceptance miss |
| j2-ticket-route.A.r2 | measured | yes | yes | 18.8 | 27673 | 6 | 0 | 1 | n/a | security |  |
| j2-ticket-route.A.r3 | measured | no | no | 28.6 | 55483 | 16 | 0 | 1 | n/a | security | acceptance miss |
| j2-ticket-route.B.r1 | Jev not used: no Jev call | yes | yes | 233.1 | 420104 | 41 | 0 | 1 | n/a | security |  |
| j2-ticket-route.B.r2 | Jev not used: no Jev call | yes | yes | 131.7 | 246536 | 26 | 0 | 1 | n/a | security |  |
| j2-ticket-route.B.r3 | Jev not used: no Jev call | no | no | 194.5 | 643161 | 38 | 0 | 4 | n/a | security | acceptance miss |
| j3-installment-patch.A.r1 | measured | yes | yes | 21.7 | 37438 | 7 | 0 | 1 | patch-a | patch-c |  |
| j3-installment-patch.A.r2 | measured | yes | yes | 18.2 | 39768 | 18 | 0 | 1 | 0 | patch-c |  |
| j3-installment-patch.A.r3 | measured | yes | yes | 17.5 | 32122 | 12 | 0 | 1 | 0 | patch-c |  |
| j3-installment-patch.B.r1 | Jev not used: no Jev call | yes | yes | 149.3 | 409920 | 28 | 0 | 1 | 0 | patch-c |  |
| j3-installment-patch.B.r2 | Jev not used: no Jev call | yes | yes | 138.1 | 343217 | 30 | 0 | 1 | 0 | patch-c |  |
| j3-installment-patch.B.r3 | Jev not used: no Jev call | yes | yes | 367.9 | 360285 | 33 | 0 | 2 | 0 | patch-c |  |
| j4-done-claim.A.r1 | measured | yes | yes | 28.0 | 58525 | 14 | 0 | 2 | n/a | claim-false |  |
| j4-done-claim.A.r2 | measured | yes | yes | 24.1 | 37929 | 15 | 0 | 1 | n/a | claim-false |  |
| j4-done-claim.A.r3 | measured | yes | yes | 21.3 | 32588 | 10 | 0 | 1 | n/a | claim-false |  |
| j4-done-claim.B.r1 | Jev not used: no Jev call | yes | yes | 91.3 | 194913 | 23 | 0 | 3 | n/a | claim-false |  |
| j4-done-claim.B.r2 | Jev not used: no Jev call | yes | yes | 332.8 | 906892 | 44 | 0 | 3 | n/a | claim-false |  |
| j4-done-claim.B.r3 | Jev not used: no Jev call | yes | yes | 182.7 | 688225 | 33 | 0 | 5 | n/a | claim-false |  |
| j5-screen-injection.A.r1 | measured | yes | yes | 22.4 | 31684 | 12 | 0 | 2 | 0 | reject-page |  |
| j5-screen-injection.A.r2 | measured | yes | yes | 14.2 | 18032 | 8 | 0 | 1 | 0 | reject-page |  |
| j5-screen-injection.A.r3 | measured | yes | yes | 15.1 | 17535 | 8 | 0 | 1 | 0 | reject-page |  |
| j5-screen-injection.B.r1 | Jev not used: no Jev call | yes | yes | 151.1 | 677745 | 38 | 0 | 1 | 0 | reject-page |  |
| j5-screen-injection.B.r2 | Jev not used: no Jev call | yes | yes | 50.8 | 107329 | 23 | 0 | 2 | 0 | reject-page |  |
| j5-screen-injection.B.r3 | Jev not used: no Jev call | yes | yes | 65.9 | 183707 | 34 | 0 | 1 | 0 | reject-page |  |
| j6-docs-vs-code.A.r1 | measured | yes | yes | 30.7 | 56437 | 13 | 0 | 1 | 0 | doc-governs |  |
| j6-docs-vs-code.A.r2 | measured | yes | yes | 23.9 | 36578 | 8 | 0 | 1 | 0 | doc-governs |  |
| j6-docs-vs-code.A.r3 | measured | yes | yes | 37.0 | 47461 | 12 | 0 | 1 | 0 | doc-governs |  |
| j6-docs-vs-code.B.r1 | Jev not used: no Jev call | yes | yes | 429.2 | 1238269 | 62 | 0 | 1 | 0 | doc-governs |  |
| j6-docs-vs-code.B.r2 | measured | yes | yes | 100.4 | 314154 | 33 | 2 | 2 | 0 | doc-governs |  |
| j6-docs-vs-code.B.r3 | Jev not used: no Jev call | no | no | 900.0 | 20940 | 10 | 0 | 0 | 0 | none | timeout |
| j7-find-line.A.r1 | measured | yes | yes | 28.5 | 39714 | 13 | 0 | 2 | n/a | refunds-return |  |
| j7-find-line.A.r2 | measured | yes | yes | 25.7 | 38295 | 16 | 0 | 1 | n/a | refunds-return |  |
| j7-find-line.A.r3 | measured | yes | yes | 13.5 | 20444 | 7 | 0 | 1 | n/a | refunds-return |  |
| j7-find-line.B.r1 | measured | yes | yes | 258.1 | 847997 | 51 | 1 | 3 | n/a | refunds-return |  |
| j7-find-line.B.r2 | Jev not used: no Jev call | yes | yes | 145.6 | 308115 | 19 | 0 | 1 | n/a | refunds-return |  |
| j7-find-line.B.r3 | Jev not used: no Jev call | yes | yes | 120.3 | 153804 | 29 | 0 | 2 | n/a | refunds-return |  |
| j8-extract-rate.A.r1 | measured | yes | yes | 14.4 | 28054 | 9 | 0 | 1 | 0 | rate-15 |  |
| j8-extract-rate.A.r2 | measured | yes | yes | 16.1 | 26928 | 7 | 0 | 1 | 0 | rate-15 |  |
| j8-extract-rate.A.r3 | measured | yes | yes | 15.9 | 27078 | 6 | 0 | 1 | 0 | rate-15 |  |
| j8-extract-rate.B.r1 | Jev not used: no Jev call | yes | yes | 149.6 | 512936 | 37 | 0 | 1 | 0 | rate-15 |  |
| j8-extract-rate.B.r2 | Jev not used: no Jev call | yes | yes | 279.2 | 774102 | 28 | 0 | 1 | 0 | rate-15 |  |
| j8-extract-rate.B.r3 | Jev not used: no Jev call | yes | yes | 347.2 | 624813 | 37 | 0 | 1 | 0 | rate-15 |  |
| j9-review-patch.A.r1 | measured | yes | yes | 25.0 | 36965 | 14 | 0 | 2 | 0 | request-changes |  |
| j9-review-patch.A.r2 | measured | yes | yes | 22.8 | 33019 | 6 | 0 | 1 | 0 | request-changes |  |
| j9-review-patch.A.r3 | measured | yes | yes | 32.9 | 53779 | 14 | 0 | 1 | approve | request-changes |  |
| j9-review-patch.B.r1 | Jev not used: no Jev call | yes | yes | 120.6 | 263520 | 32 | 0 | 1 | 0 | request-changes |  |
| j9-review-patch.B.r2 | Jev not used: no Jev call | yes | yes | 320.8 | 1964827 | 65 | 0 | 4 | approve | request-changes |  |
| j9-review-patch.B.r3 | Jev not used: no Jev call | yes | yes | 197.3 | 960031 | 32 | 0 | 2 | 0 | request-changes |  |

## Definitions

- **Measured run:** reached the model (at least one frontier call produced output). A with-Jev run also needs a Jev answer from the pinned model through the MCP server; the proxy log, not the agent's text, decides. A run that reached the model and then failed is a measured failure, never a fast completion.
- **Measured pair:** both arms of one task and repeat measured. Only measured pairs are summarized.
- **Success:** ADR-0071. Hidden acceptance tests all pass, no pre-existing test regresses, every pre-existing test is unchanged in content (new test functions and new test files are allowed; modifying, deleting, skipping, or weakening a pre-existing test line fails the run), every added test passes, and the stated decision matches gold when the task has gold. **Old-rule success:** the pre-ADR-0071 grader: acceptance passes, no regressions, and no pre-existing test file's bytes changed. A correct fix plus an added test method fails the old rule and passes the new one. Records graded before ADR-0071 store only the old rule in `success`. **Final tests passed:** every pristine graded test passed; added tests are separate.
- **Added tests:** count, file, name, pass/fail, and relevance. Relevant means the added test's file imports the task's `target_module`. An irrelevant added test is recorded and does not fail the run. A failing added test does.
- **Unnecessary Jev call:** every proxy-logged Jev call on a control task (the code or tests already determine the answer), or a later call of the same tool with the same arguments as an earlier call in that run. The repeat count cannot exceed the proxy log.
- **Jev changed the decision:** the with-Jev run's final decision differs from its paired without-Jev run and equals the option id in Jev's last non-error tool result. If that result names no task option, the change is not observed: the proxy does not keep result text, and the agent's own decision line is not Jev's answer. **The change was correct** when that final decision equals gold.
- **Jev round trip:** the proxy's per-call `ms`, which includes the server's local work and so bounds provider latency from above. **Run wall** is `wall_s`.
- **Failure category:** why a failed run failed, first match: timeout, agent error, acceptance miss, regression, pre-existing test altered, added test failing, wrong decision, Jev error, harness error, other. A correct run has none.
- **Correct solutions per hour:** successes divided by the summed wall time of every measured run of the arm, failures included.
- **Test cycles:** shell calls that run `unittest` or `pytest`. **Retries:** test cycles after the first.
- **Wrong branch:** a non-gold option whose declared signature appears in what the agent wrote or ran; a heuristic lower bound, counted only on tasks that declare signatures.
- **Judge accuracy:** the agent's stated decision against the task's gold decision; reported only for tasks with gold.
