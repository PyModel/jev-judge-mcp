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
  call: `not_found`, `not_a_file`, `binary_file`, `secret_file`, `file_too_large`,
  `path_outside_scope`. A path whose resolved name is a known secret store — every `*.env` dotenv
  and the dot-prefixed `.env` / `.env.*` family (except the `.env.example`/`.env.sample`/
  `.env.template` stand-ins), `*.pem`, `*.key`, `*.p12`, `*.pfx`,
  `id_rsa`/`id_ed25519`/`id_ecdsa`, `.npmrc`, `.pypirc`, `.netrc` — refuses `secret_file` before
  any read: asking about a config file must not ship its live secrets to the provider. What is
  read is redacted with the ADR-0076 credential-literal detector before it becomes state. A
  refusal is a verdict about the input, never a judgment. The score-array bounds are
  published-schema rejects; the noul and choice shapes are Records whose per-entry bounds the
  argument parser cannot express (ADR-0022), so the tool refuses a shape that breaks them typed
  `invalid_arguments` before any provider call — the same reject, one layer down — and noul's
  optional outcome descriptions share the instructions bound.
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
  reads, the per-file size cap, and a hard surviving-files cap, with known secret stores — the
  file tool's rule, unchanged — skipped `secret_file` rather than failing the batch. Every pruned
  path is reported in `skipped` with a stable reason (`outside_scope`, `skipped_directory`,
  `not_found`, `empty`, `too_large`, `binary`, `secret_file`, `over_the_file_cap`). Surviving
  files get one provider call each under the ADR-0069 in-flight cap; a per-file failure lands in
  `skipped` as `call_failed:<code>` and does not fail the batch; `results` is input-order stable;
  usage sums across calls. Picking among the per-file answers stays `jev_find` fed those answers
  as candidates — no new pick tool. Two shape bounds keep a real tree inside one call: glob
  expansion walks pruned (a skip-listed directory is never scanned, not filtered afterwards) and
  stops at `discovery_max` candidates; reading stops once `files_max` survivors exist; and past
  `skip_rows_max` listed rows the overflow collapses into one aggregate row per reason — count
  plus the first few paths — so a huge tree cannot flood the agent's context.
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
- **`hook compact-cut` — an opt-in compaction cut point.** The PreCompact event cannot inject
  instructions: its documented output contract is block-only — exit code 2 or a top-level
  `"decision": "block"` — Claude Code discards a PreCompact hook's `systemMessage` and `continue`
  fields, and `custom_instructions` on that event is input-only, the user's own `/compact`
  arguments (https://code.claude.com/docs/en/hooks). The cut point therefore rides the one
  documented injection path: `jev-judge-mcp hook compact-cut` handles the SessionStart event with
  matcher `compact`, which fires after a compaction completes. It reads the event's
  `transcript_path`, clips the user turns, asks one Choice keyed by real transcript turn ids —
  which turn starts the live work — and returns one line of `additionalContext` naming that turn
  and its text, so the summary keeps the live task. It abstains with no provider call when the
  source is not `compact`, without a usable transcript, or with fewer than two turns; a provider
  error, a missing or malformed answer, or a below-floor confidence is silence, never a wrong
  turn. It emits no prose beyond the one line. Default off; `JEV_HOOK_REQUIRED` does not apply —
  SessionStart has no ask decision to escalate to. There is no turn-end advisor and no
  `compact_now` tool: they need harness extension surfaces this repository deliberately does not
  ship, and an MCP tool cannot see the host's context usage — a fake. Re-opening that needs a new
  decision.
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
| `jev_files_judge` | binary sniff | 8,000 bytes | shared with `jev_file_judge`, same NUL scan |
| `jev_files_judge` | `discovery_max` | 8 × `files_max` (512) | glob expansion walks pruned and stops at the bound; the unexpanded remainder is one aggregate row, never thousands |
| `jev_files_judge` | `skip_rows_max` | 64 | past it the skip list collapses into one aggregate row per reason (count plus the first few paths), so a huge tree cannot flood the agent's context |
| `jev_ask` | `questions_min`/`questions_max` | 1–20 | one call judges one state; twenty narrow questions stay a single provider request while batching stays encouraged |
| `jev_ask` | `files_max` | 20 | an ask composes several state parts, so files keep a small share of the window |
| `jev_ask` | `state_units_max` | 20,000 | own state is framing, not a document dump; bulk material belongs in `paths` |
| `jev_ask` | `command_output_units_max` | 30,000 | room beside own state and file parts inside one request |
| `jev_ask` | `request_units_max` | 120,000 | the composed aggregate stays under the 64k-token window at worst-case encoding; over it the split refusal fires |
| `jev_ask` | command timeout | 30 s | the same budget the hook passes its provider call; a longer run belongs in the agent's own shell |
| `hook screen` | `SCREEN_INPUT_CHARS` | 6,000 | caps the judged prefix of tool output; typical reads are far smaller and the call stays cheap |
| `hook compact-cut` | `turns_max` | 20 | covers any realistic session segment while bounding the state |
| `hook compact-cut` | `turn_units_max` | 1,000 | a clipped summary per turn keeps the whole state a small fraction of one request; the returned line stays far under the host's 10,000-character inline `additionalContext` window |
| `hook compact-cut` | `transcript_tail_bytes` | 8 MiB (8,388,608) | the newest history is what a cut point needs; the read is bounded before any parsing, and a first line cut by the seek drops |

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

## Amendment (2026-10-01): command execution is off by default, denylisted, scrubbed, and bounded

The gated command is the one surface where the server does something instead of judging, so its
default flips and three deterministic defenses land ahead of the gate:

- **Off unless the operator enables it.** With no `JEV_ASK_COMMANDS=1` in the server environment,
  a `command` argument is the typed `command_disabled` refusal — zero provider construction, zero
  execution. A read-only effect is not a safe command: `curl -d @.env`, `cat ~/.ssh/id_rsa`, and
  `printenv` are all read-only by the effect question, so the harness's permission system, not the
  gate, stays the default control. Enabling the flag lets the server run what Jev judges read-only
  without a harness prompt — that trade is the operator's, made in the server environment.
- **A deterministic denylist runs before the gate.** Even when enabled, a command that names a
  network client (curl, wget, nc/ncat/netcat, ssh, scp, sftp, rsync, ftp, telnet, socat), touches
  a path `file_state` refuses as a secret store, references `~/.ssh`, `~/.aws`, `~/.config`,
  `~/.pi`, `~/.claude`, or `~/.codex` (tilde, expanded, or any path carrying one of those names as
  a directory component), or calls `env`, `printenv`, `set`, or `export` refuses `command_refused`
  with no provider call. The denylist is a conservative word-and-path match, not a shell parser.
- **The child env is scrubbed.** The process group runs with every configured secret variable
  (`Settings.named_secrets()`) removed from its environment, and every configured secret value
  joins the output redactions — in the judged command text as well as the captured output.
- **Capture is bounded.** The output pipes are drained with a cap (three bytes per UTF-16 unit —
  the worst-case encoding — proves the decoded text is over the cap), and the process group is
  killed as soon as the cap passes: a `yes` flood can no longer buffer until the timeout. An
  over-cap run refuses `output_too_large` as before, now without the buffering in between.

The `jev_ask` tool stays on the harness allow lists with commands off: the tool is a judgment
surface; execution is a separate, operator-gated surface inside it.

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
