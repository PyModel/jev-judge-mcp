# Writing states and questions — a caller guide

How you shape a state and word a question moves the probabilities you get back. This page collects
the measured guidance, so every call an agent writes gets the benefit without relearning it the hard
way. It complements [`docs/skills/jev-mcp/SKILL.md`](skills/jev-mcp/SKILL.md) (which tool fits which
step) and [`docs/reference/limits.md`](reference/limits.md) (the frozen caps and defaults).
[`src/jev_judge_mcp/skills/jev/SKILL.md`](../src/jev_judge_mcp/skills/jev/SKILL.md) is the other skill: building an app on the Jev API.
Its cookbook thresholds are not this server's defaults; see [`src/jev_judge_mcp/skills/jev/PROVENANCE.md`](../src/jev_judge_mcp/skills/jev/PROVENANCE.md).

**Where the guidance comes from.** Official pages, cited by URL and not copied here:
[how to build](https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md),
[state](https://docs.typesafe.ai/concepts/state.md),
[primitives](https://docs.typesafe.ai/primitives.md),
[confidence](https://docs.typesafe.ai/confidence.md), and
[jev-1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md).
This server's own recorded evidence lives in [`docs/evals/README.md`](evals/README.md) and
[`docs/EVIDENCE.md`](EVIDENCE.md). Do not quote another project's accuracy numbers as Jev's.

## When not to call

The on-demand rule, the high-value calls, and the skip cases are in [`docs/agent-rules.md`](agent-rules.md).

The jaggedness page names nine failure modes for `jev-1.13`: literal reading; math and numbers;
date and time comparison; indirection; large state full of irrelevant detail; adversarial content;
contradictory instructions and criteria; structural invariants that are not identities (a Noul
threshold does not carry to a Choice, and `P(q) + P(not q)` need not be 1); and generation. Avoid
asking for something code can compute, hiding several judgments in one question, System Two tasks,
and extra state the question does not need. The page is
https://docs.typesafe.ai/model-jaggedness/jev-1.13.md.

## Shape the state so nothing has to be counted

A state is evidence, not a prompt ([`docs/CONTEXT.md`](CONTEXT.md)). The official state page says
to keep facts in `state` and the judgment in the question, and to point at nested fields with
backticked paths. Two rules follow:

- **Name things; do not make the model count.** Prefer an object with named fields, or an array
  whose items carry explicit `id`s, and point at nested fields with backticked paths like
  `` `ticket.messages[0].text` ``. Counting, dates, and exact lookups belong in code
  (https://docs.typesafe.ai/model-jaggedness/jev-1.13.md). The tools that take a list of records
  (evidence items, candidates, classify items) let you give each one an id — use them, and keep ids
  short and stable. Claims (`jev_verify`, `jev_gate`) are positional strings — the schema rejects
  objects — so keep each claim self-contained and in a stable order.
- **Send what the question needs, and no more.** Extra unrelated state lowers accuracy. The API
  budget is 64k tokens for the request and 32k for `state` plus the longest question
  (https://docs.typesafe.ai/models.md). An input inside this server's UTF-16 caps can still exceed
  that window. The server does not count tokens, so that failure comes back as `provider`,
  not `input_too_large`. This server also truncates some inputs at a UTF-16 cap and marks the cut
  ([`docs/reference/limits.md`](reference/limits.md)); a judgment over cut context never gets action
  `auto`. When you must cut, cut at a boundary you can defend — a section, a function, a message —
  not a blind character count.

## Write options that separate, with a catch-all that deserves its name

- **Describe lookalike options.** A short description is what separates options that are easy to
  confuse (https://docs.typesafe.ai/primitives/choice.md). A description may be `null` when the name
  is already clear. A missing description does not separate lookalikes.
- **Keep a generic option and a catch-all apart.** If the list may not cover the input, add an
  `other` or `none` option and say in its description what belongs there — and nothing else.
  `jev_decide` ships its own escape hatches (`ask_user`, `investigate`, `none`), so a decision among
  candidates you wrote down does not need your own `other`.

## Domain rules go in the question; deterministic rules stay in code

Jev reads the question literally. Encode domain rules and boundary cases in the instructions and in
the option or level descriptions
(https://docs.typesafe.ai/models.md, https://docs.typesafe.ai/model-jaggedness/jev-1.13.md).
Counting, dates, and control flow are deterministic rules and stay in your code
(https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md). This server already splits that
second job from policy: the model judges, policy decides (ADR-0002). If a question needs "unless",
"except", or "but only when" to be answerable, it is several questions.

## One pass is not multi-step

No single-pass model does reliable multi-step arithmetic or chained lookups in one question. Split
the judgment: one question per independent factor, combined in code, where weights and order are
yours to change without re-asking. Batch every question that shares a state into one call —
questions in one request cannot see each other's answers — and ask a second round only when the
first answer is needed to gather the next evidence.

## Threshold on what the answer actually gives you

- A **noul** answer gives you a probability; threshold it. 0.5 is uncertainty, not "medium".
- A **choice** answer gives you `probabilities` and a `confidence` (how peaked the distribution is —
  not how likely it is correct). Threshold `confidence`; that is the docs' default, and you can
  define your own (https://docs.typesafe.ai/confidence.md). This server's classify, compare, and
  extract policy also thresholds the top probability and the margin; verify and gate threshold
  `confidence`. For statistical rules, read the distribution, not just the argmax.
- A **score** answer gives you a probability-weighted position; threshold it against levels, and do
  not interpolate a magnitude between them.
- Unknown confidence never meets a threshold; a fail-closed answer keeps its row off `auto`.

**Make the bar proportional to the risk.** The tools' thresholds (`auto_accept`, `review_at`,
`minimum_margin`, `block_at`) are uniform: a judgment clears `auto` at the same bar whatever you do
next. The action is not risk-aware — you are. Before a reversible step, the tool's `auto` can be
enough; before a destructive or irreversible one, demand more. A worked caller program is in
[`examples/risk_proportional_thresholds.py`](../examples/risk_proportional_thresholds.py): it reads
a decision result and requires a stricter bar before an irreversible step than a reversible one.
Per-call thresholds are documented per tool in [`docs/reference/limits.md`](reference/limits.md).

What is certified on Jev traffic so far — and what is not — is recorded in
[`docs/EVIDENCE.md`](EVIDENCE.md). The frozen defaults are parity defaults from the reference
implementation, not operating points fitted on your traffic. Official guidance is to pin the versioned model id once you tune a threshold against a version (https://docs.typesafe.ai/models.md).
This server still defaults to `jev-latest`. Live evals pin `jev-1.13.0`; the default stays the alias
because these bars are parity values, not tuned on that version. `calibrate` reports a threshold and
tells you to pin the model you measured; it does not change the default.
