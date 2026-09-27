# Provenance

This directory is the project's renamed copy of the MIT-licensed skill at
https://github.com/aaddrick/building-with-typesafe-jev commit `c1802b24bb94`.
Upstream skill name: `building-with-typesafe-jev`. This copy's name: `jev`
("Building with Jev"). `LICENSE` is that upstream MIT license.

`SKILL.md`, `api-reference.md`, `patterns.md`, and `prior-art/` are verbatim.
Not shipped: the upstream eval harness and recorded runs, assets, fonts, images,
translated READMEs, plugin manifests, scripts, its tests, its CI, and
`agents/openai.yaml` (Codex plugin metadata; it does not help an MCP client).

## Two skills

`jev` is for building an app that calls the Jev API (Choice, Score, Noul, the
SDK). Calling this MCP server's tools is the other skill, `jev-mcp`
(`docs/skills/jev-mcp/SKILL.md`, which is the same file as `src/jev_judge_mcp/skills/jev-mcp/SKILL.md`, resource `jev-skill://jev-mcp/SKILL.md`,
prompt `jev-mcp`). They do not substitute for each other. Sibling paths in this
skill (`api-reference.md`, `patterns.md`, `prior-art/INDEX.md`) are resources
under `jev-skill://jev/`.

Jev is on demand only; call it when an independent judgment materially improves the decision; never route every judgment through it. High-value calls: before a done claim, jev_gate; before reading fetched or pasted external text, jev_screen; checking another agent's report or research claims, jev_verify. Skip it when the answer is already determined by a test, type-check, or the code itself; when the choice is trivial or cheap to reverse; when the question cannot be enumerated into bounded options; or when the same unchanged decision was already asked. That is the role of both skills. The verbatim `SKILL.md` was not edited; it already says to add Jev only where code needs a judgment.

## Contradictions

Checked 2026-09-27. Live pages were fetched through the local docs gateway from
https://docs.typesafe.ai (`llms.txt`, `api.md`, `models.md`, `primitives.md`,
`primitives/score.md`, `primitives/noul.md`, `confidence.md`,
`sdk/python/usage.md`, `patterns/confidence-routing.md`). Local snapshot:
`docs/jev_docs/primitives.md`, `docs/jev_docs/models.md`. This server:
`src/jev_judge_mcp/policy/thresholds.py`, `src/jev_judge_mcp/limits.py`,
`src/jev_judge_mcp/domain/questions.py`. The skill text was not edited.

Agreed, so not listed below: price ($0.042 / million input tokens, output free),
rate limits (250,000 tokens/s, 1,200 requests/min, change without notice),
context (64k per request, 32k for state plus the longest question), aliases
(`jev-latest` and `jev-preview` both `jev-1.13.0` on the models page), text-only
input, no per-account fine-tune, Noul has no confidence, Choice max 255, and
the four pattern names.

### Against the live docs

- `api-reference.md` says a key with whitespace raises `TypeSafeError` at
  construction. Live `sdk/python/usage.md` strips leading and trailing
  whitespace, including newlines from a key file. It rejects an empty key,
  internal whitespace, control characters, and non-ASCII. An explicit empty
  key does not fall back to the environment.
- `api-reference.md` says the confidence formulas are not documented. Live
  `confidence.md` documents the Choice formula in its explorer,
  `(n × peak − 1) / (n − 1)`. The Score formula on that page is still only
  described as a statistic of the distribution, not the fitted expression in
  `api-reference.md`.
- `api-reference.md` lists latency as "Most queries complete in about 100 ms".
  The models page fetched the same day does not state a latency.
- `api-reference.md` states Score criteria as min 2, max 10. Live `api.md` says
  a Score should have at least two levels and the API accepts up to 10. That is
  softer than a hard minimum of 2. `primitives/score.md` also says at least two
  and up to 10.
- `patterns.md` cites the dates cookbook as `date_extraction` in the techniques
  section and `date_extraction_cookbook` in the index. Live `llms.txt` lists
  `date_extraction_cookbook` only.

### Against `docs/jev_docs`

- `docs/jev_docs/primitives.md` says a Score of 1–10 levels is the documented
  space and a one-level Score is legal. `api-reference.md` says min 2. This
  server's `ScoreQuestion` rejects fewer than two before sending, so the skill
  matches the sender and contradicts that local snapshot.

### Against this server

These are boundaries, not edits to the skill. The skill describes the Jev API.
This server's tools are a different surface.

- Noul `criteria` is optional in `api-reference.md` and in the live API
  examples. `NoulQuestion` always sends `criteria`. A Noul with no criteria is
  not representable on this server.
- `patterns.md` cookbook bands (Noul 0.30/0.70, Choice abstain 0.60,
  hierarchical fallback 0.9, RAG 0.70/0.45/0.55, guardrail review 0.35 / action
  0.70) are not this server's action defaults. Those defaults are
  `auto_accept` 0.8 and `review_at` cap 0.5 for verify, review, and gate;
  `auto_accept` 0.85 and `minimum_margin` 0.5 for classify, compare, and
  extract; composite floor 0.7; screen block 0.75 and review 0.25; exists
  found at 0.7 and absent below 0.35. A tool argument can override some of
  them. Copying a cookbook number does not change `action` unless that argument
  is set.
- `patterns.md` confidence-gated routing gives each action its own bar by what
  a wrong action costs. This server's `auto` bar does not rise because the next
  step is destructive. That bar is the caller's to raise, outside the server.
- Choice max 255 is the API cap, not a tool cap. This server rejects above 250
  classes (`jev_classify`) or candidates (`jev_find`, `jev_rerank`), above 6
  options (`jev_decide`, minimum 2), and above 20 candidates per field
  (`jev_extract`).
- The 64k/32k token budgets are the API's. This server truncates or rejects at
  the UTF-16 caps in `limits.py` (for example 50,000 units on a gate diff or
  an extract document). A call sized to the API budget can still be cut here.
- `api-reference.md` documents the Vercel AI Gateway for the SDK. This server
  does not emulate that provider (ADR-0007). OpenRouter in the skill is the
  SDK's `base_url`; this server's OpenRouter path is its own provider, not that
  client constructor.
- SDK retries of 429/529 are the SDK's. This server owns its own provider
  retries (ADR-0057). Do not add a second retry loop around a tool call because
  the skill says the SDK retries.
