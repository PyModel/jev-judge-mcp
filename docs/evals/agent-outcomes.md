# Agent outcome study: does Jev improve a coding agent while it uses the Jev MCP tools?

Same agent, same task, same repository revision, with and without the Jev MCP server; each task hinges on a judgment a Jev tool is for, and the with-Jev arm is told to use that tool. Raw per-run records: `evals/reports/agent-outcomes/` (gitignored).

History, not this study: `p8-pilot.md` (2026-09-21; no run called a Jev tool) and `bench150.md` (accuracy unscored, item rate is evaluator overhead) measured something else and are kept as recorded.

## claude

- **agent:** 2.1.281 (Claude Code)
- **Jev revision:** `a9dc14712ca5e1cf231e37d44feb6be589675525+dirty`; **fixture sha256:** `0c7d6b910b303c395ca5d0864355034d6981b51ee27393ce954fcfce5e0f7d70`
- **hardware:** macOS-27.0-arm64-arm-64bit, arm64, 18 CPUs
- **timeout:** 900s wall per run
- **harness server (both arms):** `request_human_review`
- **system addendum (both arms):** You are working in a small Python repository. Complete the task in the user's message, run the test suite to check your work, and stop when you are done. Use whichever of your available tools help.
- **Jev (arm B only):** `python -m jev_mcp`, `JEV_PROVIDER=typesafe`, `JEV_MCP_MODEL=jev-1.13.0`
- **grader:** pristine pre-existing tests + hidden acceptance tests (`evals/ab/grade.py`)
- **model:** `claude-sonnet-5`, effort `medium`
- **temperature:** not settable through the Claude Code CLI; its default, identical in every arm
- **tools:** Bash, Read, Edit, Write, Glob, Grep
- **limits:** 40 turns, $2.00 (`--max-budget-usd`)

Runs recorded: 18. Pairs: 9. Measured pairs: 9.

### Outcomes over 9 measured pairs

| measure | A: without Jev | B: with Jev MCP |
|---|---|---|
| tasks solved | 6/9 | 6/9 |
| time to a correct solution, s | median 14.6 (n=6, min 8.8, max 16.1) [8.8, 12.7, 14.6, 14.6, 15.2, 16.1] | median 18.3 (n=6, min 14.2, max 27.2) [14.2, 16.1, 17.4, 19.1, 25.1, 27.2] |
| correct solutions per hour | 169.28 | 102.09 |
| final tests passed | 6/9 | 6/9 |
| wrong branches (runs with one / runs on tasks with signatures; total) | 0/6; 0 | 0/6; 0 |
| test cycles per run | median 1.0 (n=9, min 1.0, max 1.0) [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0] | median 1.0 (n=9, min 1.0, max 1.0) [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0] |
| retries (total) | 0 | 0 |
| tool calls per solved task | 7.8 | 8.8 |
| tokens per solved task (context + output) | 139210 | 245407 |
| Jev calls (total; runs with one) | 0; 0/9 | 9; 9/9 |
| judge accuracy (decision = gold) | 9/9 | 9/9 |

### Paired

- Pairs: 9. Solved by both: 6; only without Jev: 0; only with Jev: 0; neither: 3.
- Wall time, with minus without, pairs both solved (s): median 4.6 (n=6, min 1.3, max 12.5) [1.3, 3.4, 3.9, 5.4, 10.5, 12.5].
- Descriptive only: no significance test, and n is small.

### Runs

| run | measurement | success | tests passed | wall s | tokens | tool calls | Jev calls | test cycles | wrong branches | decision |
|---|---|---|---|---|---|---|---|---|---|---|
| j1-refund-window.A.r1 | measured | yes | yes | 12.7 | 96722 | 7 | 0 | 1 | 0 | delivery-date-inclusive |
| j1-refund-window.A.r2 | measured | yes | yes | 8.8 | 79063 | 4 | 0 | 1 | 0 | delivery-date-inclusive |
| j1-refund-window.A.r3 | measured | yes | yes | 16.1 | 96601 | 7 | 0 | 1 | 0 | delivery-date-inclusive |
| j1-refund-window.B.r1 | measured | yes | yes | 16.1 | 165002 | 8 | 1 | 1 | 0 | delivery-date-inclusive |
| j1-refund-window.B.r2 | measured | yes | yes | 14.2 | 134453 | 4 | 1 | 1 | 0 | delivery-date-inclusive |
| j1-refund-window.B.r3 | measured | yes | yes | 17.4 | 134074 | 5 | 1 | 1 | 0 | delivery-date-inclusive |
| j2-ticket-route.A.r1 | measured | no | no | 16.1 | 77821 | 3 | 0 | 1 | n/a | security |
| j2-ticket-route.A.r2 | measured | no | no | 17.2 | 118010 | 5 | 0 | 1 | n/a | security |
| j2-ticket-route.A.r3 | measured | no | no | 12.1 | 78252 | 3 | 0 | 1 | n/a | security |
| j2-ticket-route.B.r1 | measured | no | no | 40.3 | 190580 | 7 | 1 | 1 | n/a | security |
| j2-ticket-route.B.r2 | measured | no | no | 29.3 | 195273 | 6 | 1 | 1 | n/a | security |
| j2-ticket-route.B.r3 | measured | no | no | 22.8 | 189700 | 6 | 1 | 1 | n/a | security |
| j3-installment-patch.A.r1 | measured | yes | yes | 15.2 | 133724 | 7 | 0 | 1 | 0 | patch-c |
| j3-installment-patch.A.r2 | measured | yes | yes | 14.6 | 76645 | 4 | 0 | 1 | 0 | patch-c |
| j3-installment-patch.A.r3 | measured | yes | yes | 14.6 | 78421 | 7 | 0 | 1 | 0 | patch-c |
| j3-installment-patch.B.r1 | measured | yes | yes | 19.1 | 136905 | 4 | 1 | 1 | 0 | patch-c |
| j3-installment-patch.B.r2 | measured | yes | yes | 25.1 | 162677 | 5 | 1 | 1 | 0 | patch-c |
| j3-installment-patch.B.r3 | measured | yes | yes | 27.2 | 163780 | 8 | 1 | 1 | 0 | patch-c |

## pi

- **agent:** 0.87.1
- **Jev revision:** `a9dc14712ca5e1cf231e37d44feb6be589675525`; **fixture sha256:** `0c7d6b910b303c395ca5d0864355034d6981b51ee27393ce954fcfce5e0f7d70`
- **hardware:** macOS-27.0-arm64-arm-64bit, arm64, 18 CPUs
- **timeout:** 900s wall per run
- **harness server (both arms):** `request_human_review`
- **system addendum (both arms):** You are working in a small Python repository. Complete the task in the user's message, run the test suite to check your work, and stop when you are done. Use whichever of your available tools help.
- **Jev (arm B only):** `python -m jev_mcp`, `JEV_PROVIDER=typesafe`, `JEV_MCP_MODEL=jev-1.13.0`
- **grader:** pristine pre-existing tests + hidden acceptance tests (`evals/ab/grade.py`)
- **model:** `ds4/glm-5.3-flash`, thinking `high`
- **temperature:** Pi's default for the model, identical in both arms
- **tools:** Pi's built-in tools + `pi-mcp-adapter` (loaded in both arms)
- **limits:** no turn or token cap exposed by `pi --print`; the timeout bounds the run

Runs recorded: 18. Pairs: 9. Measured pairs: 8.

Excluded pairs (not evidence of any Jev effect):

- 1 x B: Jev not used: no Jev call

### Outcomes over 8 measured pairs

| measure | A: without Jev | B: with Jev MCP |
|---|---|---|
| tasks solved | 6/8 | 6/8 |
| time to a correct solution, s | median 49.4 (n=6, min 34.3, max 54.2) [34.3, 39.7, 48.5, 50.2, 54.1, 54.2] | median 127.8 (n=6, min 107.3, max 242.8) [107.3, 116.6, 127.6, 128.0, 142.3, 242.8] |
| correct solutions per hour | 56.00 | 17.84 |
| final tests passed | 6/8 | 6/8 |
| wrong branches (runs with one / runs on tasks with signatures; total) | 0/6; 0 | 0/6; 0 |
| test cycles per run | median 1.0 (n=8, min 1.0, max 1.0) [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0] | median 1.0 (n=8, min 1.0, max 1.0) [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0] |
| retries (total) | 0 | 0 |
| tool calls per solved task | 6.0 | 15.2 |
| tokens per solved task (context + output) | 26650 | 93413 |
| Jev calls (total; runs with one) | 0; 0/8 | 8; 8/8 |
| judge accuracy (decision = gold) | 8/8 | 8/8 |

### Paired

- Pairs: 8. Solved by both: 6; only without Jev: 0; only with Jev: 0; neither: 2.
- Wall time, with minus without, pairs both solved (s): median 85.8 (n=6, min 53.2, max 188.5) [53.2, 76.9, 79.5, 92.1, 93.3, 188.5].
- Descriptive only: no significance test, and n is small.

### Runs

| run | measurement | success | tests passed | wall s | tokens | tool calls | Jev calls | test cycles | wrong branches | decision |
|---|---|---|---|---|---|---|---|---|---|---|
| j1-refund-window.A.r1 | measured | yes | yes | 34.3 | 22817 | 6 | 0 | 1 | 0 | delivery-date-inclusive |
| j1-refund-window.A.r2 | measured | yes | yes | 54.1 | 19418 | 5 | 0 | 1 | 0 | delivery-date-inclusive |
| j1-refund-window.A.r3 | measured | yes | yes | 39.7 | 17385 | 4 | 0 | 1 | 0 | delivery-date-inclusive |
| j1-refund-window.B.r1 | measured | yes | yes | 127.6 | 49699 | 11 | 1 | 1 | 0 | delivery-date-inclusive |
| j1-refund-window.B.r2 | measured | yes | yes | 107.3 | 67726 | 12 | 1 | 1 | 0 | delivery-date-inclusive |
| j1-refund-window.B.r3 | measured | yes | yes | 116.6 | 50213 | 10 | 1 | 1 | 0 | delivery-date-inclusive |
| j2-ticket-route.A.r1 | measured | no | no | 52.1 | 22680 | 5 | 0 | 1 | n/a | security |
| j2-ticket-route.A.r2 | measured | no | no | 52.5 | 26063 | 6 | 0 | 1 | n/a | security |
| j2-ticket-route.A.r3 | measured | no | no | 43.3 | 17861 | 4 | 0 | 1 | n/a | security |
| j2-ticket-route.B.r1 | measured | no | no | 180.5 | 67876 | 12 | 1 | 1 | n/a | security |
| j2-ticket-route.B.r2 | measured | no | no | 165.9 | 113566 | 17 | 1 | 1 | n/a | security |
| j2-ticket-route.B.r3 | Jev not used: no Jev call | no | no | 49.2 | 19681 | 4 | 0 | 1 | n/a | security |
| j3-installment-patch.A.r1 | measured | yes | yes | 54.2 | 21029 | 4 | 0 | 1 | 0 | patch-c |
| j3-installment-patch.A.r2 | measured | yes | yes | 48.5 | 15281 | 3 | 0 | 1 | 0 | patch-c |
| j3-installment-patch.A.r3 | measured | yes | yes | 50.2 | 15225 | 3 | 0 | 1 | 0 | patch-c |
| j3-installment-patch.B.r1 | measured | yes | yes | 242.8 | 97842 | 12 | 1 | 1 | 0 | patch-c |
| j3-installment-patch.B.r2 | measured | yes | yes | 128.0 | 40015 | 6 | 1 | 1 | 0 | patch-c |
| j3-installment-patch.B.r3 | measured | yes | yes | 142.3 | 73539 | 11 | 1 | 1 | 0 | patch-c |

## Definitions

- **Measured run:** reached the model (at least one frontier call produced output). A with-Jev run also needs a Jev answer from the pinned model through the MCP server; the proxy log, not the agent's text, decides. A run that reached the model and then failed is a measured failure, never a fast completion.
- **Measured pair:** both arms of one task and repeat measured. Only measured pairs are summarized.
- **Success:** the existing grader: every hidden acceptance test passes, no pre-existing test regresses, no pre-existing test file changed. **Final tests passed:** every graded test passed.
- **Correct solutions per hour:** successes divided by the summed wall time of every measured run of the arm, failures included.
- **Test cycles:** shell calls that run `unittest` or `pytest`. **Retries:** test cycles after the first.
- **Wrong branch:** a non-gold option whose declared signature appears in what the agent wrote or ran; a heuristic lower bound, counted only on tasks that declare signatures.
- **Judge accuracy:** the agent's stated decision against the task's gold decision; reported only for tasks with gold.
