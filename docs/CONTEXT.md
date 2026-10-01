# Jev MCP

An MCP server that turns TypeSafe's Jev model into purpose-built judgment tools for agents. Jev returns typed probabilities, not text; this server designs the questions and turns the answers into actions an agent can safely act on.

## Language

### Judgments

**Jev**:
TypeSafe's System One model: it answers typed questions about a state with probability distributions instead of generated text.
_Avoid_: the LLM, the model (when the host agent's model could be meant)

**State**:
The observed material a judgment is made about (claims, evidence, candidates, a diff). State is evidence to evaluate. Do not put instructions in it for the model to follow. That is a writing rule, not a promise the model will ignore them.
_Avoid_: context, prompt, input

**Question**:
One narrow typed ask about the state, of kind Choice, Noul, or Score. Questions in one request cannot see each other's answers.
_Avoid_: prompt, query (query is a find/rerank input field)

**Choice**:
A question whose answer is one option from a named set, with a probability for every option.
_Avoid_: enum, select, multiple-choice

**Noul**:
A question whose answer is the probability that a yes/no condition holds. A value near 0.5 means uncertain, not "medium".
_Avoid_: boolean (Vercel's wire name for it), yes/no

**Score**:
A question whose answer is a probability-weighted position on an ordered list of concrete levels, 0-indexed.
_Avoid_: rating, grade

**Answer**:
Jev's raw reply to one question, before validation.
_Avoid_: result, response (response means the whole provider reply)

**Confidence**:
How concentrated an answer's distribution is. It is not correctness, and unknown confidence never satisfies a threshold.
_Avoid_: certainty, accuracy

**Margin**:
The gap between the top and runner-up probabilities of a Choice answer.
_Avoid_: lead, spread

### Deciding

**Action**:
What the caller may do with a judgment: `auto` (stands alone), `review` (a human or stronger check should confirm), or `escalate` (must not proceed as is).
_Avoid_: decision, status, verdict

**Verdict**:
A tool's semantic conclusion about one item, such as verified / contradicted / unsupported or answered / partial / absent. A verdict is not an action.
_Avoid_: label, result

**Policy**:
The deterministic rules that turn validated answers and thresholds into actions. The model judges; policy decides.
_Avoid_: rules engine, heuristics

**Threshold**:
A caller-tunable probability or confidence boundary a policy compares against, such as auto_accept or review_at.
_Avoid_: cutoff, limit (limit means a Cap)

**Fail Closed**:
The rule that a missing, malformed, or out-of-bounds answer produces `invalid_response` and a non-auto action, never a default verdict.
_Avoid_: fallback, graceful degradation

**Truncated Context**:
A document the judgment is made over (a doc, diff, claim, or evidence item) that exceeded a cap and was cut before reaching Jev. A judgment over truncated context can never be `auto`. Cut item text (a jev_find or jev_rerank candidate, a jev_classify item or class description) is not Truncated Context: it is reported to telemetry only and does not change the action. jev_extract's capped candidate universe is a separate rule of its own field decision.
_Avoid_: partial input, clipped

**File judgment**:
A judgment whose state is a file the server reads at a caller-named path. The answer enters the payload; the file's bytes never do. A read that fails its scope or caps is a typed refusal with no provider call, never a truncated judgment.
_Avoid_: file read (the refusal is not a read), file analysis

**Reason Code**:
A stable machine-readable token explaining why a gate reached its action, such as `claims_contradicted`.
_Avoid_: error code, message

**Escape Hatch**:
An extra Choice option (ask_user, investigate, none) that lets Jev decline to pick among supplied candidates.
_Avoid_: abstain option, fallback option

**Command hook**:
An opt-in process that judges one proposed action — a shell command, or a Write or Edit — and may deny it or ask a person to review it. Silence means the hook abstained. It is not a tool.
_Avoid_: jev_gate (the completion check), calling the hook a tool

**Guard question**:
One of the typed questions the command hook sends about one proposed action: an effect Choice and a destructive-intent Noul for a shell command, a credential-in-content Noul for a Write or Edit. Repo containment and a credential-literal match are not asked; they are computed in code before any call, and a match denies on its own. Written code reaches the judge as written: only the strict credential-literal detector may rewrite it.
_Avoid_: gate question, safety prompt, permission check

**Composite**:
The weighted 0–1 summary of a patch review's four rubric scores.
_Avoid_: overall score, quality score

### Inputs

**Claim**:
An assertion to be checked against evidence. A claim is never evidence for itself or for the patch it describes.
_Avoid_: statement, assertion (in field names)

**Evidence**:
The only material a claim may be judged against.
_Avoid_: sources, context, proof

**Candidate**:
One caller-supplied option to be found, ranked, picked, or extracted. In jev_extract a candidate is a verbatim regex match, and the returned value is always one of them or null.
_Avoid_: option (wire-level term), document, hit

**Cap**:
A hard size bound on an input; exceeding it either rejects the call or truncates, depending on the field.
_Avoid_: limit, quota

**Split suggestion**:
The names and sizes of the state parts that overfilled one request, plus a first-fit grouping of them into calls that each fit. It rides the refusal that replaces a truncated judgment; it is advice, not a result.
_Avoid_: split error, batch hint

**Wire Arguments**:
The parsed arguments of one `tools/call`. Unknown keys are stripped, not refused, although every published schema says `additionalProperties: false` — the reference's zod strip, kept on purpose (ADR-0022). A missing or ill-typed declared key is the typed `invalid_arguments` refusal.
_Avoid_: strict validation, schema enforcement (the schema advertises stricter than the runtime parses, by parity)

### Providers and parity

**Provider**:
A transport that delivers questions to Jev and returns answers: TypeSafe direct, OpenRouter, Cloudflare, Vercel, or a Jev-compatible endpoint.
_Avoid_: backend, adapter, client

**Envelope**:
The provider response wrapper around the answers (answers object, usage, model). Providers validate only the envelope.
_Avoid_: payload, body

**Reference Implementation**:
The TypeScript reference server 0.5.0 whose observable behavior the Python server must match.
_Avoid_: the old server, the original, upstream

**Parity**:
Identical tool output from the Reference Implementation and the Python server given identical inputs and identical simulated answers.
_Avoid_: compatibility, equivalence

**Sanctioned Divergence**:
An intentional, ADR-recorded difference from the Reference Implementation that the parity suite expects rather than flags.
_Avoid_: known difference, deviation, bug-compat

**Extension Tool**:
A tool this server publishes beyond the Reference Implementation's frozen ten, appended after the snapshot order (ADR-0048). `jev_score`, the caller-supplied rubric, is the first.
_Avoid_: an eleventh tool (the count is not the invariant; the snapshot prefix is)

**Ask tool**:
An extension tool whose questions the caller writes: a typed Noul, Choice, or Score union instead of the server's fixed question set. The state is still evidence to evaluate, and the on-demand rule still holds; the caller owns the question, the server still owns validation and the wire.
_Avoid_: freeform tool (the shapes stay typed), prompt passthrough

**Stored Key**:
The API key file `jev-judge-mcp setup` writes after proving it live. The environment variable always wins over it.
_Avoid_: the credential (credentials are many), a config file

**Response Cache**:
The optional on-disk replay of an identical request's Evaluation, keyed by the exact wire body. Verbatim, never edited, off by default.
_Avoid_: memoization, a provider

**Reference Quirk**:
A behavior of the Reference Implementation that looks accidental and needs an explicit keep-or-diverge decision.
_Avoid_: bug, legacy behavior
