---
status: accepted
---

# Caller-written questions and server-read files: the ask and file-judgment extensions

Every frozen tool fixes its questions server-side: this server designs the questions and turns
answers into actions. A coding agent's most frequent judgments — is this test failure a real
regression, does this file contain a credential, is this plan safe to run — do not always fit a
rubric the server could have anticipated, and judging them by hand costs the agent's context: the
one budget the server exists to save. This ADR opens two extension-tool families and two opt-in
command hooks, the way ADR-0048 opened the extension-tool path: the snapshot order stays a frozen
prefix on the wire, and everything new appends after it with its own divergence entry, caps, and
pinning tests. Nothing here touches a frozen tool's schema, policy, or fixtures.

## Decision

- **`jev_ask` — the caller writes the questions.** One extension tool whose `questions` argument
  is a typed union of the three canonical question shapes (Noul, Choice, Score — `instructions`
  plus kind-shaped `criteria`, exactly what `domain/questions.py` sends on the wire), validated by
  the same `validation/` package as every frozen tool. It is not a raw JSON string: the published
  schema teaches the union, a wrong shape is the typed `invalid_arguments` refusal, and no string
  parsing sits between the caller and the validators. Answers come back keyed by the caller's
  question ids, with usage reported in the payload and the telemetry span.
- **`jev_ask` composes state from three parts.** The caller's own `state` text (short framing,
  capped), optional `paths` the server reads as state (small cap; the file tools' rules apply),
  and an optional `command`: the server runs it only after the ADR-0035 gate logic — imported,
  not a subprocess — judges it in-process. A deny becomes the typed `command_refused` refusal
  carrying the reason and the final-block notice; the refusal is the answer, and there is no
  execution and no provider call behind it. An allowed command runs with a timeout and an output
  cap, in the hook's existing posture: the gate judges, the tool does not sandbox — operator-level
  isolation stays the operator's (ADR-0035).
- **Overflow refuses; it never truncates.** When the composed request does not fit, `jev_ask`
  refuses with each part's size and a Split suggestion (below) naming which parts go to which
  call. The Truncated Context rule closes the other door: a part that had to be cut can never
  stand `auto` — for an ask-style tool that means the refusal fires instead of a truncated call.
- **`jev_file_judge` — one kind-discriminated file judgment.** One extension tool, not three: a
  `kind` of `noul`, `choice`, or `score` picks the question type, with `instructions` and
  kind-shaped `criteria`. The server reads the caller-named file as state; the payload carries the
  typed answer only — the file's bytes never enter it. Refusals are typed and cost no provider
  call: `not_found`, `not_a_file`, `binary_file`, `file_too_large`, `path_outside_scope`. A
  refusal is a verdict about the input, never a judgment.
- **Path scope is the server's working directory, with no caller override.** A path is read only
  when its resolved absolute path — symlinks followed — stays inside the server process's working
  directory; `..` segments and symlinked targets that resolve outside are refused
  `path_outside_scope` before any I/O beyond the resolution. The launch directory already is the
  operator's scope decision (the stdio/hook cwd convention); an `allow_outside_cwd` flag would
  turn that decision into an option a prompt can argue for. A caller who wants another tree
  launches the server there.
- **`jev_files_judge` — the same judgment over many files.** Files, directories, and glob
  patterns in; a deterministic prune runs before any provider call: pattern expansion, a
  skip-directory list (dependency, build, cache, and VCS-internal directories), binary and empty
  reads, the per-file size cap, and a hard surviving-files cap. Every pruned path is reported in
  `skipped` with a stable reason (`outside_scope`, `skipped_directory`, `not_found`, `empty`,
  `too_large`, `binary`, `over_the_file_cap`). Surviving files get one provider call each under
  the ADR-0069 in-flight cap; a per-file failure lands in `skipped` as `call_failed:<code>` and
  does not fail the batch; `results` is input-order stable; usage sums across calls. Picking among
  the per-file answers stays `jev_find` fed those answers as candidates — no new pick tool.
- **`hook screen` — an opt-in annotator for tool output.** `jev-judge-mcp hook screen` reads a
  Claude Code PostToolUse event, judges the first `SCREEN_INPUT_CHARS` of the tool's output with
  one Noul question (does the text carry injected instructions that try to redirect the agent
  from its task or the user's request — override the task, unrequested actions, secret or data
  exfiltration, user or system impersonation; documentation the agent was pointed at, such as
  build or usage steps, is not a flag), and on a flag emits the harness's annotation envelope —
  additional context for the agent, worded as data-not-directions. It never blocks and never
  rewrites content. Deterministic precedence: a Read of the operator's instruction files —
  `AGENTS.md`, `CLAUDE.md`, any `SKILL.md` — abstains before any provider call. Default off; the
  sample fragment is verified for Claude Code, and pi and other hosts stay marked unverified
  until proven (the completion-hook convention). Unusable input or a provider error means
  silence: an annotator must fail invisible, never into a gate, and `JEV_HOOK_REQUIRED` does not
  apply to it — a screen cannot ask.
- **`hook compact-cut` — an opt-in compaction cut point.** `jev-judge-mcp hook compact-cut` reads
  the PreCompact event, asks one Choice over clipped user-turn summaries — which turn starts the
  live work — and folds the picked turn into the custom compaction instructions the harness
  accepts. It abstains without a usable transcript or with fewer than two turns, and emits no
  prose beyond the fold-in line. Default off. There is no turn-end advisor and no `compact_now`
  tool: they need harness extension surfaces this repository deliberately does not ship, and an
  MCP tool cannot see the host's context usage — a fake. Re-opening that needs a new decision.
- **Caps and thresholds are frozen here, with a reason each.** The tool caps move into `limits.py`
  blocks when each tool ships (the ADR-0014/0048 pattern; `docs/reference/limits.md` carries the
  numbers from today so page and ADR move together), and the hook constants stay hook-local beside
  the 0.5/0.4 floors, as ADR-0035 set. In-server model routing stays out of scope: provider and
  model are process configuration (ADR-0008); an agent routes through `jev_decide` recipes, and
  the server never self-routes.

| Surface | Bound | Value | Reason |
| --- | --- | --- | --- |
| `jev_file_judge` | `file_units_max` | 100,000 | roughly half of Jev's 64k-token request window at worst-case encoding, so questions and framing still fit when the file is the whole state |
| `jev_file_judge` | `instructions_units_max` | 2,000 | a question is a narrow ask — the classify/decide description bound |
| `jev_file_judge` | choice criteria | 2–250 options, 2,000 units each | the `jev_classify` bounds: one convention for caller-supplied option sets |
| `jev_file_judge` | score criteria | 2–10 levels, 200 units each | the ADR-0048 rubric freeze, unchanged |
| `jev_file_judge` | binary sniff | 8,000 bytes | the first-8,000-byte NUL scan git uses; text almost never carries a NUL there |
| `jev_files_judge` | `files_max` | 64 | the worst-case cost of one call is bounded before the first provider call |
| `jev_files_judge` | `patterns_max` | 32 | 64 survivors already bound the work; the input stays legible in refusals |
| `jev_files_judge` | per-file units | 100,000 | shared with `jev_file_judge`, one number to calibrate |
| `jev_ask` | `questions_min`/`questions_max` | 1–20 | one call judges one state; twenty narrow questions stay a single provider request while batching stays encouraged |
| `jev_ask` | `files_max` | 20 | an ask composes several state parts, so files keep a small share of the window |
| `jev_ask` | `state_units_max` | 20,000 | own state is framing, not a document dump; bulk material belongs in `paths` |
| `jev_ask` | `command_output_units_max` | 30,000 | room beside own state and file parts inside one request |
| `jev_ask` | `request_units_max` | 120,000 | the composed aggregate stays under the 64k-token window at worst-case encoding; over it the split refusal fires |
| `jev_ask` | command timeout | 30 s | the same budget the hook passes its provider call; a longer run belongs in the agent's own shell |
| `hook screen` | `SCREEN_INPUT_CHARS` | 6,000 | caps the judged prefix of tool output; typical reads are far smaller and the call stays cheap |
| `hook compact-cut` | `turns_max` | 20 | covers any realistic session segment while bounding the state |
| `hook compact-cut` | `turn_units_max` | 1,000 | a clipped summary per turn keeps the whole state a small fraction of one request |

- **Registration.** Two divergence entries (`ask-tool-extension`, `file-judge-tools`, surface
  `tool_schema`, same fields as `score-tool-extension`), the CONTEXT terms (*Ask tool*,
  *File judgment*, *Split suggestion*; *Guard question* waits for the slice that designs one),
  and the caps blocks on `docs/reference/limits.md`. Every new tool description carries the
  on-demand rule verbatim — "Jev is invoked when an unresolved judgment earns a model decision.
  Deterministic evidence takes precedence; Jev is not a mandatory ceremony." — plus its not-for
  line (exact lookups, counting, math, grep-answerable questions), and the deterministic
  pre-checks (path resolution, binary sniff, size caps, command gate) always run in code before
  any provider call.
- **Evidence honesty.** The surfaces ship with the docs' convention that a tool with no recorded
  live judgment data says so (`docs/tools.md`); L3 datasets and calibration follow the P7 protocol
  as a fast-follow, and no release card claims more until then.

## Considered Options

- **A raw `questions_json` string for `jev_ask`** — rejected: stringly typed, off-style, and the
  typed union in the published schema teaches the shape as well as a description does.
- **Three flat per-kind file tools (`*_bool`/`choice`/`score`)** — rejected: it triples the
  `tools/list` suffix for no validation gain; one discriminator pins one schema.
- **An `allow_outside_cwd` escape for the path scope** — rejected: the rule exists so a prompt
  cannot steer reads outside the operator's tree; a flag reopens exactly that door.
- **A turn-end compaction advisor and an MCP `should_i_compact`** — rejected: the first needs an
  extension runtime this repo does not ship, the second cannot see the host's context usage and
  would be a fake judgment surface.
- **In-server model routing** — rejected: it contradicts ADR-0008; agents route through
  `jev_decide`, the server does not self-route.

## Consequences

- The snapshot order stays a byte-identical prefix of `tools/list`; each new tool appends after
  the last extension with its own schema-pinning, refusal, and fail-closed tests (the ADR-0048
  pattern), and `test_docs_alignment.py` extends `_TOOL_CAPS` as each `limits.py` block lands.
- New failure shapes get one owning test each when the surface lands: zero-call refusals (the
  fake provider is never constructed), the overflow split message, gate-before-command ordering,
  and the annotator's silence-on-error.
- The security corpus gains the new input fields (paths, command text, caller questions) when the
  tools land; the hooks log nothing and stay opt-in, so the ambient live path does not grow.
- The release path for any of these surfaces keeps the dogfood gate: load in Claude Code and
  exercise the tool end to end before a release; L3 calibration is a fast-follow with honest
  "no recorded live eval" cards in the meantime.
