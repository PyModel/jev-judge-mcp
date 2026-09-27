# Primitives

Repo-authored summary of TypeSafe's question types, in our own words. Last checked against the live
pages on 2026-09-27; the live pages win where they differ.

Three question types. Mix them in one `evaluate` call. Every question sees the same `state`, runs in parallel, and cannot see the others' answers. Question IDs are for your code; Jev never sees them. Put the whole meaning in `instructions`.

| Type | Ask when | Criteria | Answer |
| --- | --- | --- | --- |
| **noul** | Yes/no; the probability is the signal | Optional `{true, false}` | `noul` in `[0,1]`. No `confidence`. |
| **choice** | One of a closed set, unordered | Required map of option → description (a description may be `null` when the name says it all). At most 255 options. | `choice` (argmax), `probabilities` (sum ≈ 1), `confidence` |
| **score** | Position on an ordered spectrum | Required array, low → high. 2–10 levels: at least two, and the API takes up to 10. | `score` (probability-weighted, may land between levels), `legend`, `probabilities`, `confidence` |

Prefer the type your code can act on: noul → `if`, choice → switch, score → threshold.

## Snap judgment

Ask what a knowledgeable person decides in a second given the right context. "Does this message convey urgency?" is a question. "Analyze this and determine the best course of action" is not — split it.

If several independent factors matter, one question per factor, combine in code. Weights live in code so they change without a re-ask.

## State

`state` is the evidence: string, JSON object, or array of text. Object with named fields for anything with parts. Text only — no images/audio/video. English is strongest; other languages (including CJK) are weaker.

Keep facts in `state` and judgments in questions. Filter in code first; extra unrelated state costs accuracy (context rot). Point at nested fields with backticked paths: `` `ticket.messages[0].text` ``.

## Instructions and criteria

- `instructions` may be a string, object, or array. Structure when the question has labeled parts, a schema, or a comparison list.
- Choice/Score/Noul criteria values accept the same shapes. Use `{what, not_for, examples}` to draw boundaries.
- Phrase noul so high ≈ yes. `noul ≈ 0.5` means yes and no are similar, not "medium intensity". Do not use noul for skill/severity — that is a score.
- Choice: include `other` / `none` when the list may not cover the input. Give the full list rather than a shortlist; descriptions separate lookalike options.
- Score levels are judged independently against the state. Numbering is 0-indexed array position. `score` is Σ(level × P(level)); do not treat a fractional score as a reconstructed magnitude.
- Align instructions with criteria. A noul whose `true` means no will underperform.

## Batching

Send every question that uses this state in one call, including speculative ones whose answers you will ignore on some branches. Extra questions add tokens, not a round trip. A second request is warranted only when the first answer is needed to fetch evidence, build new state, or choose the next options.

Context budget (jev-1.13): 64k tokens for the whole request; 32k for `state` plus the longest single question.

## Confidence vs probability

Choice/Score `confidence` collapses the shape of `probabilities` into `[0,1]`. Peaked → high; flat → low. It is not overall workflow correctness. Noul has no separate confidence — the value *is* the probability.

Thresholds are yours and should scale with risk. TypeSafe's suggested starting pattern splits confidence into three bands (act, confirm, hand off), with a floor around 0.5 below which you do not act and a higher bar for destructive actions; tune every number on your own data. Cookbook numbers are examples, not defaults. If you only need the best option and the choice is harmless, take argmax. For a statistical rule, use `probabilities`.

Do not carry a noul threshold onto a choice. A choice is relative (which option); each noul is absolute (can be low for every option). `P(noul)` and `1 - P(not noul)` are not guaranteed to sum to 1.

## In this repo

`jev_score` takes 2–10 levels (ADR-0048), and the score question refuses fewer than two before any
request is sent (`src/jev_judge_mcp/domain/questions.py`). Choice-based tools stay under the 255-option
ceiling by their own caps (`docs/reference/limits.md`).

Official: [primitives](https://docs.typesafe.ai/primitives.md) · [confidence](https://docs.typesafe.ai/confidence.md)
