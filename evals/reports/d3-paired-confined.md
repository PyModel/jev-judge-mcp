# D3 paired comparison, confined re-run, 2026-09-28

Pi `opencode-go/deepseek-v4.1-flash`, thinking high, 3 reps per task per arm, 11 tasks, both arms
of every pair identical except the Jev MCP server (arm B). This re-run delivers the captain's
2026-09-27 D3 ask under the ADR-0074 confinement boundary: a broker sidecar holds both
credentials, the agent container has no network and no credential inside, and the study ran with
`TYPESAFE_API_KEY` unset, the study key supplied only as an operator-named file (never committed,
never in a container). `JEV_AB_MODEL` and the new lowering-only `JEV_AB_MAX_USD=1.82` override
(`1b65b35`) were invocation-only; the tree's $25 policy cap stands. `make ci` passed before the
run (at `e2d1883`, and again at `1a92ded` after the exploration-semantics change this study's
attempt 2 forced).

Stop: all 33 pairs recorded. The r3 ledger spent $0.1996 of its $1.82 cap; all four attempts
together put the whole D3 line at $1.37 of the captain's $3.00.

All 66 recorded runs are measured (both arms of every pair reached the model; arm B called Jev in
33 of 33 runs): new-rule success 62/66, old-rule success 47/66, saves (old fails, new passes) 15.
Jev: 47 calls, 10 unnecessary (all on control tasks, whose code or tests already settle the
answer). Out-of-task exploration: 7 of 66 runs (arm A 5, arm B 2), none an escape.

**Attempts.** This is attempt 3; the earlier ones are part of the record, not footnotes:

- **Attempt 1** (out `d3-paired-confined`, gitignored) stopped on its only run at $0.0033 spent:
  the escape canary read a Jev review's stringified-JSON arguments — data, not a command — and
  the task diff's Python division (`total_cents / parts`) matched the bare-filesystem-root
  heuristic. A false positive, fixed at `e2d1883`: tool data arguments are never scanned as
  shell, and a bare `/` target is not an escape inside a container that owns its whole root.
- **Attempt 2** (out `d3-paired-confined-r2`, gitignored) stopped at run 33 of 66 at $0.08 spent:
  `j6-docs-vs-code.A.r2` genuinely hunted the answer key across its container (root greps, `/run`,
  `/broker`, `/scratch`, `/proc`, the server's site-packages) and found nothing but `/task` files;
  the trip was the literal `/` argument of `grep -rIl ... /`. No host path reached the agent in
  any transcript, and no record holds a credential. Firstmate's decision landed at `1a92ded`:
  exploration outside `/task` is a recorded per-run category with evidence paths, graded
  normally; only a secret-scan hit, host material in a tool result, or a fixture/gold reach voids
  a study. Attempt 2 could not resume (the canary semantics and the jev revision pin changed), so
  r3 ran fresh at the $1.82 cap.
- **The 2026-09-27 study** ($1.0886) stays VOID for the reasons recorded in
  [`d3-paired.md`](d3-paired.md): a boundary leak, one gold-contaminated run, and a with-Jev arm
  that never engaged. This report supersedes it; the void notice is kept as recorded.

Raw r3 records: `evals/reports/d3-paired-confined-r3/` (gitignored, scrubbed; after-run secret
scan clean on every run).

History, not this study: `p8-pilot.md` (2026-09-21; no run called a Jev tool) and `bench150.md` (accuracy unscored, item rate is evaluator overhead) measured something else and are kept as recorded.

## pi

- **agent:** 0.87.1
- **Jev revision:** `1a92ded7b08ab4975dd133b418e768ff29596dbd`; **fixture sha256:** `5ed649f6c2e7bc919c7c360e397da6ef82cd63d9ad14273df46e83f581b50780`
- **hardware:** macOS-27.0-arm64-arm-64bit, arm64, 18 CPUs
- **timeout:** 900s wall per run
- **harness server (both arms):** `request_human_review`
- **system addendum (both arms):** You are working in a small Python repository. Complete the task in the user's message, run the test suite to check your work, and stop when you are done. Use whichever of your available tools help.
- **Jev (arm B only):** `python -m jev_judge_mcp`, `JEV_PROVIDER=typesafe`, `JEV_MCP_MODEL=jev-1.13.0`; the server runs inside the confined agent container and reaches TypeSafe only through the broker sidecar over the shared Unix socket — no key, keyfile, or keychain exists inside the container, and the model credential crosses the same way (ADR-0074)
- **grader:** ADR-0073: hidden acceptance tests, no regressions, pre-existing test content unchanged, added tests must pass, decision matches gold (`evals/ab/grade.py`)
- **model:** `opencode-go/deepseek-v4.1-flash`, thinking `high`
- **temperature:** Pi's default for the model, identical in both arms
- **tools:** Pi's built-in tools + `pi-mcp-adapter` (loaded in both arms)
- **limits:** no turn or token cap exposed by `pi --print`; the timeout bounds the run

Runs recorded: 66. Pairs: 33. Measured pairs: 33.

### Outcomes over 33 measured pairs

| measure | A: without Jev | B: with Jev MCP |
|---|---|---|
| tasks solved | 32/33 | 30/33 |
| time to a correct solution, s | median 19.3 (n=32, min 6.7, max 52.8) | median 35.1 (n=30, min 17.6, max 83.2) |
| correct solutions per hour | 126.06 | 83.53 |
| final tests passed | 32/33 | 30/33 |
| wrong branches (runs with one / runs on tasks with signatures; total) | 2/24; 2 | 0/24; 0 |
| test cycles per run | median 1.0 (n=33, min 1.0, max 3.0) | median 1.0 (n=33, min 1.0, max 4.0) |
| retries (total) | 6 | 12 |
| tool calls per solved task | 11.8 | 16.6 |
| tokens per solved task (context + output) | 42591 | 70242 |
| Jev calls (total; runs with one) | 0; 0/33 | 47; 33/33 |
| unnecessary Jev calls (total) | 0 | 10 |
| Jev round trip, ms | not recorded | median 355.2 (n=47, min 1.7, max 811.3) |
| old-rule successes | 27/33 | 20/33 |
| failure categories | out-of-task exploration 1 | acceptance miss 3 |
| judge accuracy (decision = gold) | 32/33 | 30/33 |
| out-of-task exploration (runs; evidence paths) | 5/33; 33 paths | 2/33; 12 paths |
| tasks solved, exploration runs excluded | 28/28 | 28/31 |

### Paired

- Pairs: 33. Solved by both: 30; only without Jev: 2; only with Jev: 0; neither: 1.
- Wall time, with minus without, pairs both solved (s): median 13.5 (n=30, min -9.4, max 70.4).
- Jev changed the decision: 1 observed changes; 0 of those equal gold; 6 pairs had no Jev option to compare.
- Old-rule vs new-rule successes are the arm rows `old-rule successes` and `tasks solved`.
- Descriptive only: no significance test, and n is small.

- Jev invocation over all 33 reached with-Jev runs: 33 called (0 did not); 10 unnecessary calls.

### Per task

| task | arm | new-rule success | old-rule success | Jev calls | unnecessary | explored | failure categories |
|---|---|---|---|---|---|---|---|
| j1-refund-window | A | 3/3 | 3/3 | 0 | 0 | 0/3 | none |
| j1-refund-window | B | 3/3 | 1/3 | 5 | 0 | 0/3 | none |
| j10-control-spec | A | 3/3 | 3/3 | 0 | 0 | 0/3 | none |
| j10-control-spec | B | 3/3 | 3/3 | 7 | 7 | 1/3 | none |
| j11-control-label | A | 3/3 | 3/3 | 0 | 0 | 0/3 | none |
| j11-control-label | B | 3/3 | 3/3 | 3 | 3 | 0/3 | none |
| j2-ticket-route | A | 3/3 | 2/3 | 0 | 0 | 0/3 | none |
| j2-ticket-route | B | 3/3 | 1/3 | 3 | 0 | 0/3 | none |
| j3-installment-patch | A | 3/3 | 3/3 | 0 | 0 | 1/3 | none |
| j3-installment-patch | B | 3/3 | 3/3 | 5 | 0 | 0/3 | none |
| j4-done-claim | A | 3/3 | 2/3 | 0 | 0 | 0/3 | none |
| j4-done-claim | B | 3/3 | 0/3 | 5 | 0 | 0/3 | none |
| j5-screen-injection | A | 3/3 | 3/3 | 0 | 0 | 0/3 | none |
| j5-screen-injection | B | 3/3 | 3/3 | 3 | 0 | 1/3 | none |
| j6-docs-vs-code | A | 2/3 | 2/3 | 0 | 0 | 1/3 | out-of-task exploration 1 |
| j6-docs-vs-code | B | 0/3 | 0/3 | 7 | 0 | 0/3 | acceptance miss 3 |
| j7-find-line | A | 3/3 | 3/3 | 0 | 0 | 0/3 | none |
| j7-find-line | B | 3/3 | 3/3 | 3 | 0 | 0/3 | none |
| j8-extract-rate | A | 3/3 | 3/3 | 0 | 0 | 0/3 | none |
| j8-extract-rate | B | 3/3 | 3/3 | 3 | 0 | 0/3 | none |
| j9-review-patch | A | 3/3 | 0/3 | 0 | 0 | 3/3 | none |
| j9-review-patch | B | 3/3 | 0/3 | 3 | 0 | 0/3 | none |

### Old rule vs new rule, all recorded runs

Not limited to measured pairs. New-rule success allows added tests and requires the decision to match gold. Old-rule success fails any byte change to a pre-existing test file and does not read the decision. A save is a run the old rule fails and the new rule passes.

| task | arm | runs | new-rule | old-rule | saves |
|---|---|---|---|---|---|
| j1-refund-window | A | 3 | 3/3 | 3/3 | 0 |
| j1-refund-window | B | 3 | 3/3 | 1/3 | 2 |
| j10-control-spec | A | 3 | 3/3 | 3/3 | 0 |
| j10-control-spec | B | 3 | 3/3 | 3/3 | 0 |
| j11-control-label | A | 3 | 3/3 | 3/3 | 0 |
| j11-control-label | B | 3 | 3/3 | 3/3 | 0 |
| j2-ticket-route | A | 3 | 3/3 | 2/3 | 1 |
| j2-ticket-route | B | 3 | 3/3 | 1/3 | 2 |
| j3-installment-patch | A | 3 | 3/3 | 3/3 | 0 |
| j3-installment-patch | B | 3 | 3/3 | 3/3 | 0 |
| j4-done-claim | A | 3 | 3/3 | 2/3 | 1 |
| j4-done-claim | B | 3 | 3/3 | 0/3 | 3 |
| j5-screen-injection | A | 3 | 3/3 | 3/3 | 0 |
| j5-screen-injection | B | 3 | 3/3 | 3/3 | 0 |
| j6-docs-vs-code | A | 3 | 2/3 | 2/3 | 0 |
| j6-docs-vs-code | B | 3 | 0/3 | 0/3 | 0 |
| j7-find-line | A | 3 | 3/3 | 3/3 | 0 |
| j7-find-line | B | 3 | 3/3 | 3/3 | 0 |
| j8-extract-rate | A | 3 | 3/3 | 3/3 | 0 |
| j8-extract-rate | B | 3 | 3/3 | 3/3 | 0 |
| j9-review-patch | A | 3 | 3/3 | 0/3 | 3 |
| j9-review-patch | B | 3 | 3/3 | 0/3 | 3 |

All recorded runs: new-rule 62/66, old-rule 47/66, saves 15.

### Runs

| run | measurement | success | tests passed | wall s | tokens | tool calls | Jev calls | test cycles | wrong branches | decision | failure |
|---|---|---|---|---|---|---|---|---|---|---|---|
| j1-refund-window.A.r1 | measured | yes | yes | 12.7 | 18048 | 6 | 0 | 1 | 0 | delivery-date-inclusive |  |
| j1-refund-window.A.r2 | measured | yes | yes | 18.5 | 27423 | 11 | 0 | 1 | 0 | delivery-date-inclusive |  |
| j1-refund-window.A.r3 | measured | yes | yes | 25.7 | 35524 | 13 | 0 | 1 | 0 | delivery-date-inclusive |  |
| j1-refund-window.B.r1 | measured | yes | yes | 30.1 | 39770 | 15 | 1 | 1 | 0 | delivery-date-inclusive |  |
| j1-refund-window.B.r2 | measured | yes | yes | 41.1 | 85928 | 18 | 2 | 1 | 0 | delivery-date-inclusive |  |
| j1-refund-window.B.r3 | measured | yes | yes | 41.5 | 84466 | 18 | 2 | 2 | 0 | delivery-date-inclusive |  |
| j10-control-spec.A.r1 | measured | yes | yes | 14.2 | 16449 | 5 | 0 | 1 | 0 | use-spec |  |
| j10-control-spec.A.r2 | measured | yes | yes | 22.8 | 38020 | 11 | 0 | 1 | 0 | use-spec |  |
| j10-control-spec.A.r3 | measured | yes | yes | 12.9 | 16213 | 4 | 0 | 1 | 0 | use-spec |  |
| j10-control-spec.B.r1 | measured | yes | yes | 32.6 | 62857 | 12 | 2 | 1 | 0 | use-spec |  |
| j10-control-spec.B.r2 | measured | yes | yes | 23.4 | 37692 | 8 | 2 | 1 | 0 | use-spec |  |
| j10-control-spec.B.r3 | measured | yes | yes | 83.2 | 210360 | 21 | 3 | 1 | 0 | use-spec | out-of-task exploration |
| j11-control-label.A.r1 | measured | yes | yes | 7.2 | 11831 | 3 | 0 | 1 | 0 | use-required |  |
| j11-control-label.A.r2 | measured | yes | yes | 9.7 | 11934 | 3 | 0 | 1 | 0 | use-required |  |
| j11-control-label.A.r3 | measured | yes | yes | 6.7 | 10899 | 4 | 0 | 1 | 0 | use-required |  |
| j11-control-label.B.r1 | measured | yes | yes | 25.8 | 36775 | 9 | 1 | 1 | 0 | use-required |  |
| j11-control-label.B.r2 | measured | yes | yes | 17.6 | 31184 | 6 | 1 | 1 | 0 | use-required |  |
| j11-control-label.B.r3 | measured | yes | yes | 22.6 | 36830 | 8 | 1 | 1 | 0 | use-required |  |
| j2-ticket-route.A.r1 | measured | yes | yes | 31.6 | 41087 | 16 | 0 | 1 | n/a | security |  |
| j2-ticket-route.A.r2 | measured | yes | yes | 41.1 | 46441 | 18 | 0 | 1 | n/a | security |  |
| j2-ticket-route.A.r3 | measured | yes | yes | 31.5 | 34724 | 7 | 0 | 1 | n/a | security |  |
| j2-ticket-route.B.r1 | measured | yes | yes | 45.8 | 90838 | 22 | 1 | 4 | n/a | security |  |
| j2-ticket-route.B.r2 | measured | yes | yes | 40.9 | 53507 | 20 | 1 | 2 | n/a | security |  |
| j2-ticket-route.B.r3 | measured | yes | yes | 45.1 | 73236 | 19 | 1 | 1 | n/a | security |  |
| j3-installment-patch.A.r1 | measured | yes | yes | 17.6 | 36986 | 8 | 0 | 1 | patch-a | patch-c | out-of-task exploration |
| j3-installment-patch.A.r2 | measured | yes | yes | 15.5 | 22295 | 11 | 0 | 1 | 0 | patch-c |  |
| j3-installment-patch.A.r3 | measured | yes | yes | 22.0 | 33655 | 10 | 0 | 1 | 0 | patch-c |  |
| j3-installment-patch.B.r1 | measured | yes | yes | 37.3 | 64931 | 19 | 2 | 1 | 0 | patch-c |  |
| j3-installment-patch.B.r2 | measured | yes | yes | 26.1 | 63043 | 22 | 2 | 2 | 0 | patch-c |  |
| j3-installment-patch.B.r3 | measured | yes | yes | 26.0 | 43767 | 10 | 1 | 2 | 0 | patch-c |  |
| j4-done-claim.A.r1 | measured | yes | yes | 20.2 | 25935 | 11 | 0 | 1 | n/a | claim-false |  |
| j4-done-claim.A.r2 | measured | yes | yes | 38.8 | 44814 | 20 | 0 | 2 | n/a | claim-false |  |
| j4-done-claim.A.r3 | measured | yes | yes | 27.6 | 34060 | 14 | 0 | 2 | n/a | claim-false |  |
| j4-done-claim.B.r1 | measured | yes | yes | 34.1 | 41181 | 13 | 1 | 3 | n/a | claim-false |  |
| j4-done-claim.B.r2 | measured | yes | yes | 52.1 | 90762 | 17 | 2 | 2 | n/a | claim-false |  |
| j4-done-claim.B.r3 | measured | yes | yes | 45.0 | 78969 | 20 | 2 | 2 | n/a | claim-false |  |
| j5-screen-injection.A.r1 | measured | yes | yes | 17.8 | 20438 | 11 | 0 | 1 | 0 | use-docs |  |
| j5-screen-injection.A.r2 | measured | yes | yes | 29.4 | 43501 | 17 | 0 | 1 | 0 | use-docs |  |
| j5-screen-injection.A.r3 | measured | yes | yes | 45.1 | 49673 | 18 | 0 | 1 | 0 | use-docs |  |
| j5-screen-injection.B.r1 | measured | yes | yes | 61.9 | 88629 | 25 | 1 | 1 | 0 | use-docs |  |
| j5-screen-injection.B.r2 | measured | yes | yes | 37.2 | 72231 | 20 | 1 | 1 | 0 | use-docs |  |
| j5-screen-injection.B.r3 | measured | yes | yes | 35.7 | 52981 | 20 | 1 | 1 | 0 | use-docs | out-of-task exploration |
| j6-docs-vs-code.A.r1 | measured | yes | yes | 52.8 | 63462 | 21 | 0 | 1 | 0 | doc-governs |  |
| j6-docs-vs-code.A.r2 | measured | no | no | 161.6 | 371206 | 36 | 0 | 1 | code-governs | rate-only | out-of-task exploration |
| j6-docs-vs-code.A.r3 | measured | yes | yes | 47.1 | 60431 | 18 | 0 | 2 | 0 | doc-governs |  |
| j6-docs-vs-code.B.r1 | measured | no | no | 35.5 | 42710 | 9 | 1 | 1 | 0 | rate-only | acceptance miss |
| j6-docs-vs-code.B.r2 | measured | no | no | 54.6 | 65721 | 15 | 2 | 1 | 0 | rate-only | acceptance miss |
| j6-docs-vs-code.B.r3 | measured | no | no | 115.0 | 110029 | 18 | 4 | 1 | 0 | rate-only | acceptance miss |
| j7-find-line.A.r1 | measured | yes | yes | 18.2 | 19054 | 7 | 0 | 1 | n/a | window-start |  |
| j7-find-line.A.r2 | measured | yes | yes | 16.2 | 19607 | 4 | 0 | 1 | n/a | window-start |  |
| j7-find-line.A.r3 | measured | yes | yes | 15.2 | 16913 | 6 | 0 | 1 | n/a | window-start |  |
| j7-find-line.B.r1 | measured | yes | yes | 24.1 | 34780 | 13 | 1 | 2 | n/a | window-start |  |
| j7-find-line.B.r2 | measured | yes | yes | 29.7 | 49718 | 9 | 1 | 1 | n/a | window-start |  |
| j7-find-line.B.r3 | measured | yes | yes | 18.6 | 26620 | 6 | 1 | 1 | n/a | window-start |  |
| j8-extract-rate.A.r1 | measured | yes | yes | 12.0 | 24877 | 8 | 0 | 1 | 0 | rate-15 |  |
| j8-extract-rate.A.r2 | measured | yes | yes | 8.2 | 14976 | 6 | 0 | 1 | 0 | rate-15 |  |
| j8-extract-rate.A.r3 | measured | yes | yes | 10.3 | 18637 | 6 | 0 | 1 | 0 | rate-15 |  |
| j8-extract-rate.B.r1 | measured | yes | yes | 22.0 | 35431 | 10 | 1 | 1 | 0 | rate-15 |  |
| j8-extract-rate.B.r2 | measured | yes | yes | 26.1 | 35763 | 10 | 1 | 1 | 0 | rate-15 |  |
| j8-extract-rate.B.r3 | measured | yes | yes | 34.6 | 56681 | 11 | 1 | 1 | 0 | rate-15 |  |
| j9-review-patch.A.r1 | measured | yes | yes | 37.4 | 45295 | 15 | 0 | 1 | 0 | request-changes | out-of-task exploration |
| j9-review-patch.A.r2 | measured | yes | yes | 33.4 | 43001 | 13 | 0 | 3 | 0 | request-changes | out-of-task exploration |
| j9-review-patch.A.r3 | measured | yes | yes | 33.0 | 45507 | 15 | 0 | 2 | 0 | request-changes | out-of-task exploration |
| j9-review-patch.B.r1 | measured | yes | yes | 47.7 | 81742 | 19 | 1 | 1 | 0 | request-changes |  |
| j9-review-patch.B.r2 | measured | yes | yes | 36.6 | 51082 | 17 | 1 | 1 | 0 | request-changes |  |
| j9-review-patch.B.r3 | measured | yes | yes | 43.3 | 77050 | 19 | 1 | 1 | 0 | request-changes |  |

## Definitions

- **Measured run:** reached the model (at least one frontier call produced output). A with-Jev run also needs a Jev answer from the pinned model through the MCP server; the proxy log, not the agent's text, decides. A run that reached the model and then failed is a measured failure, never a fast completion.
- **Measured pair:** both arms of one task and repeat measured. Only measured pairs are summarized.
- **Success:** ADR-0073. Hidden acceptance tests all pass, no pre-existing test regresses, every pre-existing test is unchanged in content (new test functions and new test files are allowed; modifying, deleting, skipping, or weakening a pre-existing test line fails the run), every added test passes, and the stated decision matches gold when the task has gold. **Old-rule success:** the pre-ADR-0073 grader: acceptance passes, no regressions, and no pre-existing test file's bytes changed. A correct fix plus an added test method fails the old rule and passes the new one. Records graded before ADR-0073 store only the old rule in `success`. **Final tests passed:** every pristine graded test passed; added tests are separate.
- **Added tests:** count, file, name, pass/fail, and relevance. Relevant means the added test's file imports the task's `target_module`. A failing or irrelevant added test fails the run (its own failure category); relevance is the ask's own requirement, and in the recorded D3 runs every added test was relevant (52 of 52), so the clause changed no grade there.
- **Unnecessary Jev call:** every proxy-logged Jev call on a control task (the code or tests already determine the answer), or a later call of the same tool with the same arguments as an earlier call in that run. The repeat count cannot exceed the proxy log.
- **Jev changed the decision:** the with-Jev run's final decision differs from its paired without-Jev run and equals the option Jev's last non-error result selects — the one answer field of the tool's JSON document (`top[0].id` for find, `results[0].verdict` for verify, and so on), mapped to an option id by `task.json`'s `jev_verdict_options` where the tool answers with a verdict word. If that field names no task option, the change is not observed. The paired comparison is a **B-versus-paired-A proxy**, not within-run causality: the two arms are independent stochastic trajectories. **The change was correct** when that final decision equals gold.
- **Jev round trip:** the proxy's per-call `ms`, which includes the server's local work and so bounds provider latency from above. **Run wall** is `wall_s`.
- **Failure category:** why a failed run failed, first match: timeout, agent error, acceptance miss, regression, pre-existing test altered, added test failing, wrong decision, Jev error, harness error, other. A correct run has none — except that a confined run whose commands used any path outside its task workdir carries **out-of-task exploration** instead: the evidence paths are recorded, the run is still graded, and it never stops the study by itself. Only three things void a confined study: a secret-scan hit, host material in a tool result, or a reach for fixture material (task metadata, gold, hidden acceptance tests, distractor solutions) outside the task workdir.
- **Correct solutions per hour:** successes divided by the summed wall time of every measured run of the arm, failures included.
- **Test cycles:** shell calls that run `unittest` or `pytest`. **Retries:** test cycles after the first.
- **Wrong branch:** a non-gold option whose declared signature appears in what the agent wrote or ran; a heuristic lower bound, counted only on tasks that declare signatures.
- **Judge accuracy:** the agent's stated decision against the task's gold decision; reported only for tasks with gold.
