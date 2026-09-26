# Writing states and questions — a caller guide

How you shape a state and word a question moves the probabilities you get back. This page collects
the measured guidance, so every call an agent writes gets the benefit without relearning it the hard
way. It complements [`docs/skills/jev-mcp/SKILL.md`](skills/jev-mcp/SKILL.md) (which tool fits which
step) and [`docs/reference/limits.md`](reference/limits.md) (the frozen caps and defaults).

**Where the numbers come from.** The magnitudes quoted below are not Jev numbers. They were measured
by [decider](https://github.com/Mapika/decider) (Apache-2.0), an open reproduction of the System One
model class, on **decider's own Qwen3.5-based fine-tuned models** — see its
[results for input shapes, option sets, and question framing](https://github.com/Mapika/decider/blob/main/docs/RESULTS.md)
and its [stated limits](https://github.com/Mapika/decider/blob/main/README.md). The directions
almost certainly transfer to any single-pass typed-decision model, including Jev; the magnitudes
were not measured on Jev and must not be quoted as Jev's. jev-mcp's own recorded evidence lives in
[`docs/evals/README.md`](evals/README.md) and [`docs/EVIDENCE.md`](EVIDENCE.md).

## Shape the state so nothing has to be counted

A state is evidence, not a prompt ([`docs/CONTEXT.md`](CONTEXT.md)). Two shape rules follow from
that, both measured by decider:

- **Name things; do not make the model count.** When decider's models had to pick one record out of
  64 in a bare array, accuracy dropped from 0.70 (one record, named by path) to 0.51. Writing the
  indices into the state as text recovered it to 0.62, still below naming the one record outright.
  Prefer an object with named fields, or an array whose items carry explicit `id`s, and point at
  nested fields with backticked paths like `` `ticket.messages[0].text` ``. Every jev-mcp tool that
  takes a list of records (evidence items, candidates, classify items, gate claims) lets you give
  each one an id — use them, and keep ids short and stable.
- **Send whole documents when the judgment depends on the whole.** Clipping an article decider's
  models read whole at 0.71 accuracy cost 21 points (0.50 when clipped to 5000 characters). jev-mcp
  truncates some inputs at a cap and marks the cut ([`docs/reference/limits.md`](reference/limits.md));
  a judgment over cut context never gets action `auto`. When you must cut, cut at a boundary you can
  defend — a section, a function, a message — not a blind character count.

## Write options that separate, with a catch-all that deserves its name

- **Describe every option.** Names alone are weak; a one-line description per option is what
  separates lookalikes. decider's terse option sets scored 0.59 before its training made the same
  buckets reach 0.86 — the labels alone were not doing the work.
- **Keep a generic option and a catch-all apart.** When decider put a broad option like `other`
  beside a generic in-scope option, in-scope items leaked into the catch-all (0.85 vs 0.95 when the
  catch-all was scoped by description). If the list may not cover the input, say in the catch-all's
  description what belongs there — and nothing else. `jev_decide` ships its own escape hatches
  (`ask_user`, `investigate`, `none`), so a decision among candidates you wrote down does not need
  your own `other`.

## Keep rules out of the question

A one-sentence question scored 0.67 where the same judgment behind a paragraph of rules scored
0.24, on decider's models: rules written into the question are read unevenly, at best. Put the
facts in the state, the meaning in the option or level descriptions, and the rules in your code.
jev-mcp already follows this: the model judges, policy decides (ADR-0002). If a question needs
"unless", "except", or "but only when" to be answerable, it is several questions.

## One pass is not multi-step

No single-pass model does reliable multi-step arithmetic or chained lookups in one question. Split
the judgment: one question per independent factor, combined in code, where weights and order are
yours to change without re-asking. Batch every question that shares a state into one call —
questions in one request cannot see each other's answers — and ask a second round only when the
first answer is needed to gather the next evidence.

## Threshold on what the answer actually gives you

- A **noul** answer gives you a probability; threshold it. 0.5 is uncertainty, not "medium".
- A **choice** answer gives you `probabilities` and a `confidence` (how peaked the distribution is —
  not how likely it is correct). Threshold the top probability, and for statistical rules read the
  distribution, not just the argmax.
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
implementation, not operating points fitted on your traffic.
