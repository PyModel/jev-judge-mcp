<!-- Source of truth: jev-judge-mcp docs/agent-rules.md. The README copy and every cap below are
     pinned by tests/contract/test_docs_alignment.py. Depth: docs/skills/jev-mcp/SKILL.md
     (which tool fits which step) and docs/guidance.md (how to shape the call). -->

### Fast judgment checks — and when to skip them

Jev (TypeSafe) is a small judgment model served by the jev-judge-mcp MCP server: its tools take
evidence plus a question with a fixed answer set and return typed probabilities, not text. Call a
`jev_*` tool (`mcp__jev__*` in Claude Code) when a step judges material you already have — a
bounded check, a pick-one, a rank, a match-the-claim — and an independent typed judgment is worth
an extra tool turn. Skip it on steps you can settle by reading what is already on screen, or that
your tests already cover: the extra turn costs agent wall time, and the recorded studies measured
agents slower with Jev, never faster.

| Tool | Use it to | Caps |
|------|-----------|--------|
| `jev_verify` | Check claims against evidence → verified / contradicted / unsupported. Subagent or research reports, PR descriptions, your own "done" claims | no length bound on claims or evidence |
| `jev_gate` | Before declaring done: the patch plus its completion claims checked against diff and test-log evidence in one call → auto / review / escalate | ≤16 claims, ≤16 evidence items; 200,000 units of evidence, 50,000 units per diff or test log |
| `jev_review` | Score a diff against the request: correctness, spec match, test gap, blast radius, `safe_to_apply` | 50,000 units per document, truncated |
| `jev_screen` | Screen fetched or pasted external text for prompt injection and relevance **before** reading it → pass / review / block / skip | no length bound |
| `jev_compare` | Two passages: same_fact / contradicts / different_facts, optional per-aspect checks. Docs vs code drift, changelog vs diff | 20,000 units per passage, ≤10 aspects |
| `jev_find` | Which of up to 250 candidates (files, notes, hits) answers the question, plus whether any candidate matches at all | ≤250 candidates, 2,000 units per candidate |
| `jev_rerank` | A relevance score for every candidate, full ordering. Triage search hits and grep results | ≤250 candidates |
| `jev_classify` | Bucket items into a shared class catalog: triage, routing, labeling | ≤64 items, ≤250 classes |
| `jev_decide` | One bounded choice among 2–6 options with evidence and priorities; escape hatches `ask_user` / `investigate` / `none` | 2–6 options |
| `jev_extract` | Your regex proposes candidates, Jev picks, the value comes back verbatim (versions, prices, dates, IDs) | 50,000 units per document, ≤32 fields |
| `jev_score` | Grade severity or risk on your own ordered rubric; threshold the level, never interpolate a magnitude between levels | 2–10 levels |

Caps are UTF-16 code units, frozen in the server's `limits.py`.

Rules:

- **Not for open work, not for trivia.** No Jev call for new prose, code, or research whose
  answers you cannot list — write those yourself. And skip Jev on steps you already know the
  answer to.
- **Evidence in, not your verdict.** State holds raw diffs, logs, and excerpts — not your
  conclusion. A conclusion written into state gets agreement, not a judgment.
- **Act on `action`:** `auto` → proceed · `review` → confirm with tests, source reading, or a
  stronger check · `escalate` → stop and surface it. `invalid_response` → the row is unjudged;
  leave it without a verdict.
- **Jev screens; it never proves.** A Jev check never replaces running the tests, lint, or types.
  A `jev_gate` `auto` is necessary before "done", not sufficient.
- **Batch.** One call with every claim, candidate, or item beats many calls; questions inside one
  request cannot see each other's answers.
- **No re-asks.** Do not re-ask an unchanged question hoping for a better answer; gather better
  evidence instead.
- **Failures are one line.** Tool error or missing key (`TYPESAFE_API_KEY`): say so in one line,
  then fall back to normal checks.
