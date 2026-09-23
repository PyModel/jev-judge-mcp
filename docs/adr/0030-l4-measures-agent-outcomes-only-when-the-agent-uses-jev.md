---
status: accepted
---
# L4 measures agent outcomes, and only from runs where the agent used Jev

Two recorded L4 results could not answer whether Jev helps a coding agent.

- **P8 pilot** (`evals/reports/p8-pilot.md`, 2026-09-21). Claude Code with `claude-sonnet-5`, three
  tasks, three arms. No B or C run called a Jev tool, and there were no permission denials. Every arm
  solved every task. The tasks never needed a judgment, so the agent had no reason to call Jev. The
  wall-time gap between arms (30.9 s against 33.7 s) is noise.
- **bench150** (`evals/reports/bench150.md`). No item was labeled, so accuracy was never scored.
  107 of the 150 with-Jev runs never reached the local model. The 43 pairs that did run measured
  evaluator overhead in items per minute, not whether the agent got better.

## Decision

- **Question.** The same agent does the same coding task with and without the Jev MCP server. `evals/ab/`
  is rewritten to answer that. There is no second framework: the rewrite reuses `evals/agent.py`,
  `evals/relay.py`, the recording proxy, `evals/spend.py`, the grader, and the bench's Pi launcher,
  parser and use gate.
- **Arms.** There are two. A runs without Jev. B runs this worktree's Python server behind the
  proxy. Arm C, the TS reference server, is dropped, because comparing servers is L2's job.
- **Agents.** Claude Code and Pi, the two agents the runner can already launch. Each agent is studied
  on its own, under its own ledger, and its results are never pooled with the other's.
- **Held constant.** Within a study, both arms share the model, effort or thinking level, temperature,
  built-in tools, harness server, user prompt, limits, timeout and grader. Arm B's system addendum
  adds one sentence that names the task's Jev tool. A test asserts that the command lines differ in
  that sentence only, and that the MCP configs differ in the `jev` server only.
- **Pinned setup.** The first run pins the Jev revision, the fixture hash, the agent version and
  setup, and the hardware. A resume under any other value is refused.
- **Tasks.** The three pilot tasks are replaced with three that each hinge on a judgment Jev's tools
  are for: a policy boundary (`jev_verify`), a ticket classification (`jev_classify`), and a choice
  among three plausible patches (`jev_decide`).
  - Each task ships its options, a gold decision that the snapshot's documents settle, and a solution
    for each wrong option. A test proves every wrong solution fails the hidden acceptance tests.
  - The gold and the grader never reach the agent's workdir.
- **Measurement.**
  - A run that never produced model output is a harness fault. It is not measured.
  - An arm-B run is measured only if the proxy logged a Jev answer from the pinned model. The agent's
    own text is not enough.
  - A run that reached the model and then failed is a measured failure.
  - A pair (task and repeat) is evidence only when both of its arms are measured. When one arm isn't,
    the whole pair is dropped, so both arms are always summarized over the same tasks.
- **Report.** The report covers measured pairs only. It gives tasks solved, the median and spread of
  time to a correct solution, correct solutions per hour, final tests passed, wrong branches, test
  cycles and retries, tool calls and tokens per solved task, Jev calls, and judge accuracy where a
  gold decision exists, plus paired counts and wall-time differences.
  - With no measured pair, the report says "not measured" and prints no numbers and no difference.
  - It never computes an item rate.
- **Spend.** The $25 cap is unchanged. The run cap is sized to the grid: 3 tasks × 2 arms × 3
  repeats = 18 runs per agent. A Pi run's worst case is its Jev headroom alone, because its model is
  local.
- **History.** `p8-pilot.md` and the bench150 reports stay as they were recorded; `p8-pilot.md` gets
  a one-line history banner. Nothing writes them any more, and they are not this study's result.

## Consequences

- P8's old headline, cost per correct task, is gone. Cost is still recorded for every run.
- Wrong branches are counted from signature regexes declared per task, over what the agent wrote or
  ran. The count is a heuristic lower bound. A task with no declared signatures reports no count.
- Retries are the test cycles after the first. A cycle's pass or fail is not read, because nothing
  verifies that the agents' tool-error flags track a test run's exit status.
- The study still costs money to run (`JEV_AB_LIVE=1 make ab AGENT=claude|pi`). ADR-0027's contract
  test guards the flag as before, now through `evals/ab/run.py`.
