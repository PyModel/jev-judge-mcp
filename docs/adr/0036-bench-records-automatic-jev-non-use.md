---
status: accepted
---
# The bench records an automatic arm that does not call Jev

ADR-0030 still governs the outcome study (`evals/ab/`): a with-Jev coding run is measured only when
the proxy logged a Jev answer, and a pair is evidence only when both arms are measured. That rule
answers "does telling an agent to use Jev change the outcome of a task."

The 150-question bench answers a different question: the same judgment items, run three ways. Applying
0030's drop rule to the middle arm would erase the observation the arm exists to collect.

## Decision

- **Arms.** A is direct: no Jev server. B is automatic: the Jev server is available and the prompt
  does not mention it. C is forced: the server plus the existing one-sentence instruction and the use
  gate. A, B, and C share the agent, model, thinking level, user prompt, limits, and timeout.
- **Automatic non-use.** A B run that never calls Jev is a result. The record says "did not call Jev".
  The answer is still scored. It is not a failure, and it does not trip the compliance stop.
- **Forced use.** A C run with no Jev answer from the pinned model fails the use gate, as the bench's
  with-Jev arm did before this split. The compliance stop counts C only.
- **What is analyzed.** Each item's three arms run back to back, in a seeded random order. Only
  complete triplets are summarized. A stop leaves complete triplets.
- **Provider failure.** A run that loses the model provider is not an arm result. The bench model
  is Pi `opencode-go/deepseek-v4.1-flash` at thinking high. One prompt preflights it. The first
  connection, auth, or rate-limit error stops the run. The attempt is booked so it is not retried.
  The 2026-09-22 two-arm run is why: after 43 with-Jev runs reached the model, the provider stopped
  accepting connections (Pi: "Connection error.", zero tokens, both arms), and the runner recorded
  the remaining 107 as if they were results.
- **Spend.** The ceiling stays 25 USD and 25 Jev calls per run, and it applies to Jev spend. Agent
  model spend is recorded from Pi's usage cost fields and marked not measured when those fields are
  absent. After 10 triplets the projected Jev spend across all 450 runs stops the study above 22.75 USD.

## Consequences

- The outcome study's arm B is unchanged. Its measurement rule stays ADR-0030.
- The bench's old arm B is arm C. Reports name A direct, B automatic, and C forced.
- Accuracy stays unmeasured until two human labelers and an adjudicator exist. This ADR does not
  create labels.

## Amendment (2026-09-23): the server is exposed eagerly and directly

The recorded 150-triplet run measured the arms through the pi-mcp-adapter's defaults: the jev
server was lazy and proxy-only, so no jev tool reached any model until it walked the gateway itself
(`mcp({server})` → `mcp({connect})` → `mcp({describe})` → `mcp({tool})`). Arm B never started that
walk (0/150 runs, one turn each, empty `tool_counts`), and arm C spent a median 4 extra turns on it:
of C's median +10.4 s over A, the Jev round trip is ~0.5 s and the rest is that discovery dance
(provider median 454.6 ms; server 0.8 ms; relay 1.4 ms). The arms were measuring the adapter's
exposure defaults, not the tools.

- **Exposure.** The bench's Pi MCP config and the installer's pi entry now set
  `lifecycle: "eager"`, `directTools: true`, and `toolPrefix: "none"` (pi-mcp-adapter 2.37.0,
  `types.ts` `ServerEntry`; verified by a live one-prompt probe: the first system message lists
  `jev_verify` … `jev_score`). omp, Pythinker, OpenCode, Codex, Cursor, and Claude Code/Desktop use
  native MCP clients that list tools on connect, so only the pi surfaces change.
- **Addendum.** The shared addendum drops "do not look for files or other sources": it suppressed
  the tool use arm B exists to observe. The wording stays identical on A and B and still never names
  Jev, so the arms are unchanged in kind.
- **Arm C's sentence** names the now-visible `jev_` tools instead of the gateway dance.
- **What does not change:** the arm definitions, the use gates, the caps, the scorer and analysis
code, and the tools/list wire (no schema, name, or description change).
