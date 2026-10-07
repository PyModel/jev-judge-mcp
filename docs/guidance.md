# Writing states and questions — a caller guide

How you shape a state and word a question moves the probabilities you get back. This page collects
the measured guidance, so every call an agent writes gets the benefit without relearning it the hard
way. It complements [`docs/skills/jev-mcp/SKILL.md`](skills/jev-mcp/SKILL.md) (which tool fits which
step) and [`docs/reference/limits.md`](reference/limits.md) (the frozen caps and defaults).
[`src/jev_judge_mcp/skills/jev/SKILL.md`](../src/jev_judge_mcp/skills/jev/SKILL.md) is the other skill: building an app on the Jev API.
Its cookbook thresholds are not this server's defaults; [`docs/reference/limits.md`](reference/limits.md) is.

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
  that window. The server does not count tokens; when the API refuses the request as over its
  window, the code is `input_too_large` (ADR-0079): split the call. This server also truncates some inputs at a UTF-16 cap and marks the cut
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

## Composite scoring: the factors from the model, the weights from code

When a verdict is a blend of independent factors — accept or reject a change on correctness, fit,
and risk together — do not ask for "the overall quality". Ask each factor its own question and
blend the answers in code. Through this server's tools that is one graded call per factor:
`jev_score` with a rubric per factor (a yes/no factor is a two-level score), or `jev_decide` when
the factor is a bounded pick; on the raw Jev API it is the same factor questions batched into one
request. The shipped precedent is `jev_review`: four rubric scores from the model, and the weights
(`REVIEW_WEIGHTS`, `src/jev_judge_mcp/policy/review.py`) applied afterward in policy, producing one
composite you can threshold. The weights are configuration, never input: they do not appear in
`state`, in a question, or in an option description, so nothing you pass can move the blend, and
retuning the blend never re-asks a judgment.

Gate the factors, not only the blend. `jev_review` escalates when the lowest rubric confidence
falls below `review_at`; a composite that averages a confident answer with a coin flip hides its
weakest leg, so demand the same of your own blend before the weighted sum counts.

**When not to use it.** When one factor decides alone, ask only that factor. When the factors are
the same judgment restated, split the state or sharpen the question instead. When the blend is
really a pick among handlers or options you can name, that is `jev_decide`, not arithmetic. And a
blend that encodes a domain rule (if risk is high, reject regardless) is policy: the rule lives in
your code, and only the judgment itself belongs to the model.

Jev is invoked when an unresolved judgment earns a model decision. Deterministic evidence takes
precedence; Jev is not a mandatory ceremony — the weights and the arithmetic are exactly the part
Jev should never see.

## Routing: a cheap bounded choice in front of an expensive step

Handing a task to the right executor — which agent, which harness, which pipeline — is a bounded
choice whenever you can name the handlers. Write them down as `jev_decide` options (2–6, with
descriptions that separate the lookalikes), put the task facts in `state`, and let the escape
hatches say what a default rule cannot: no handler fits (`none`), the facts are missing
(`investigate`), or the caller must choose (`ask_user`). If the real first question is whether the
task is startable at all, make ambiguity its own question in the same call — a `jev_score` over
levels from "specified enough to start" to "needs its own design pass" — and threshold it before
the routing answer counts.

This routes a judgment *through* the server. It is not the server routing models: which model
answers every tool is process configuration (`JEV_MCP_MODEL`, ADR-0008), one setting for the whole
process. In-server model routing is out of scope by decision — a caller that wants a cheaper model
for cheaper judgments decides that in its own configuration, never in a tool argument.

**When not to use it.** A deterministic route (a queue name, a file extension, a config entry)
stays in code — ask Jev only when the dispatch itself is the unresolved judgment. More than six
handlers is not one call: group them coarsely first (the traversal below) or fix the handler list.
And when the obvious handler is cheap to try and cheap to undo, try it instead of asking.

Jev is invoked when an unresolved judgment earns a model decision. Deterministic evidence takes
precedence; Jev is not a mandatory ceremony.

## Traversing a catalog bigger than one call

A Choice question tops out at 255 options, `jev_classify` carries at most 250 classes and 64 items
per call, and `jev_decide` picks among 2–6 — so a taxonomy of thousands of classes is a traversal,
not a call. Cut it into three judgments, each inside one call's bounds:

1. **Prune with `jev_classify`.** Write a small catalog of coarse buckets — families of classes,
   not the classes themselves — and classify the item into them. Keep every bucket whose
   probability and margin clear your bar; an unclear split is two buckets carried forward, not a
   wrong answer.
2. **Rank with `jev_rerank`.** Score the surviving classes' descriptions against the item to get
   an order. This is where a wide middle field — ten plausible families — collapses to a top few.
3. **Decide with `jev_decide`.** Put the top few candidates in front of the bounded choice, the
   escape hatches standing behind them. If step 1 already returned one class clear of
   `auto_accept` and `minimum_margin`, skip to done: the traversal exists to earn a confident
   answer, not to spend three calls where one settles it.

For a deep tree the prune step repeats: classify into level-1 buckets, then into the winning
bucket's children, until a level fits one call. Rank and decide only at the level where the
descriptions stop separating on their own.

**When not to use it.** A catalog that fits one call is one `jev_classify` call. Classes your code
can look up exactly stay in code. And an open-ended set you cannot enumerate is not a catalog —
name the space differently or write the answer yourself.

Jev is invoked when an unresolved judgment earns a model decision. Deterministic evidence takes
precedence; Jev is not a mandatory ceremony.

## Threshold on what the answer actually gives you

- A **noul** answer gives you a probability; threshold it. 0.5 is uncertainty, not "medium".
- A **choice** answer gives you `probabilities` and a `confidence` (how peaked the distribution is —
  not how likely it is correct). Threshold `confidence`; that is the docs' default, and you can
  define your own (https://docs.typesafe.ai/confidence.md). This server's classify, compare, and
  extract threshold the top probability and the margin, not `confidence`; verify and gate threshold
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
