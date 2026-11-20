# Tool cards — intended use, measured evidence, weak spots

One card per published tool: what it is for, what this repo's own recorded evidence says about it,
and when not to use it. Claims here come only from recorded results
([`docs/evals/README.md`](evals/README.md), [`evals/README.md`](../evals/README.md)) and from
behavior the code and ADRs freeze. Where nothing is recorded, the card says so: "no recorded live
eval" is a statement about evidence, not a verdict that the tool works. Routing (which tool fits
which step) is in [`docs/skills/jev-mcp/SKILL.md`](skills/jev-mcp/SKILL.md); caps and defaults in
[`docs/reference/limits.md`](reference/limits.md). Building an app on the Jev API is the other skill,
[`src/jev_judge_mcp/skills/jev/SKILL.md`](../src/jev_judge_mcp/skills/jev/SKILL.md), and its cookbook thresholds are not these defaults.
The on-demand rule is in [`docs/agent-rules.md`](agent-rules.md).

The one recorded live judgment-quality run to date (2026-09-23, pinned `jev-1.13.0`, synthetic
datasets, 3 cases per tool) covers `jev_classify` and `jev_verify` only. Every other tool below is
unmeasured live; its "weak spots" are frozen behaviors and documented gaps, not measured failure
rates. Certified operating points: none yet ([`docs/EVIDENCE.md`](EVIDENCE.md)).

The `model` field is the requested model on the TypeSafe provider (`jev-latest` unless
`JEV_MCP_MODEL` is set) and the slug sent on OpenRouter (`typesafe/jev-1.13` for `jev-latest`).
The compatible and Cloudflare providers report a valid body `model` when available and otherwise
report the model sent. All providers prefer a valid body request ID, then check the response
headers, and omit `request_id` if neither supplies one. Upstream metadata strings must be
nonempty, at most 1,024 characters, and printable ASCII (ADR-0057). A `jev-latest` caller on
TypeSafe cannot see which version answered (ADR-0001 parity). UTF-16 caps below govern tool
admission. The provider enforces its context window of 64k tokens per request and 32k for state
plus the longest question. A provider context refusal returns `input_too_large` with split advice.
File-list and range gates hold any files from a rejected claims group for review (ADR-0079, ADR-0086).

Every tool's schema advertises `additionalProperties: false`; the runtime keeps the reference's
zod strip: unknown keys are stripped, not refused — a missing or ill-typed declared key is refused
typed `invalid_arguments` (ADR-0022).

Errors include `invalid_arguments` for schema refusals, `input_too_large` for input or context
limits, `auth` for authentication and configuration, `quota` for rate limiting, `timeout` for
provider timeouts, `provider` for other provider failures, and `internal` for unexpected tool
failures. Tools can also return codes for their own refusals. An `internal` error does not establish
provider contact and makes a required completion hook ask.

---

## Caller IDs

Find, rerank, classify, decide, extract, verify, gate, and gate range use canonical caller IDs.
The sanitizer replaces each run outside ASCII letters, digits, dots, hyphens, and underscores
with `_`, trims edge underscores, and keeps the first 64 characters. A supplied value that
sanitizes to empty gets a generated ID. Two supplied IDs with the same sanitized value, including
an empty value, or a supplied ID that matches a reserved key return `invalid_arguments` before
any provider request. The error names the original IDs and the shared or reserved key.

Where IDs are optional, an omitted ID gets its usual positional ID, such as `candidate0`, when
available. If that key is taken, the tool picks the next free index starting at the list length.
Supplied IDs take precedence in either order. Results that include caller IDs use canonical IDs.
For exact round trips, send unique IDs that already follow the sanitizer's character and length
rules and have no leading or trailing underscores.
See [ADR-0085](adr/0085-shared-caller-id-policy.md).

## jev_verify — claims against evidence

**Use when** you hold claims (a summary, a PR description, an agent's "done" list) and the evidence
they cite, and want verified / contradicted / unsupported per claim. Act on an `auto` verdict.
An `auto` contradiction is confidently false. `stands` is true only for a verified claim at
`auto`. A sole caller note counts as the policy source without filling `supporting_evidence`.
A caller note alone cannot earn `auto`. If a source was requested, a missing or malformed source
answer holds the claim for review. [Caller evidence IDs](#caller-ids) reserve `none` when
multiple evidence items are sent.

**Measured.** L3 live run, 2026-09-23: n=5 claims, contradiction_recall 1.000, macro_f1 1.000,
brier 0.00004, ece 0.002, selective_accuracy 1.000, auto_coverage 1.000, invalid 0. Five synthetic
questions; no accuracy claim beyond them.

**Weak spots.** Claims, evidence, and their lengths are deliberately uncapped, so cost and latency
scale with what you send (README § Operator notes). That is not a token budget: an input inside the
missing cap can still exceed 64k tokens and come back as `input_too_large` (ADR-0079): split the call. Policy has two tiers only — `auto` and
`review`; there is no escalate (manifest `policy.verify_action`). An unsupported or contradicted
claim can carry a `missing_evidence` code (`single_item_no_source`, `needs_diff`, `needs_tests`,
`needs_before_after`) that names what evidence would settle it. With exactly one evidence item no
source question is asked (frozen reference behavior), so rows carry no `evidence_ids` and
`supporting_evidence` is null — attribution is trivially that item; consumers who need per-item
attribution should send multiple evidence items or read `missing_evidence`.

**Not for** judging a claim you cannot supply evidence for, or producing analysis text.

## jev_screen — read a fetched page before reading it

**Use when** an agent is about to ingest external text and needs injection risk and relevance first.

**Measured.** No recorded live eval. The scorer exists (`injection_recall_at_false_block_rate` at a
fixed false-block rate; `evals/README.md`), but the fixed false-block rate has no specified value,
so no run was recorded.

**Weak spots.** The screened text is the state. jev-1.13 does not treat state as hostile by
default, so instructions in it can move the answer
(https://docs.typesafe.ai/model-jaggedness/jev-1.13.md). The injection score is a signal, not a
guarantee. ADR-0068 adds that warning to verify's question; this card is the screen warning, and
the question text is unchanged. Text length has no UTF-16 cap, and an over-long page can still
exceed the 64k token window and come back as `input_too_large` (ADR-0079): split the call. The `skip`
thresholds for substance and relevance are hardcoded at 0.3 in the reference and are not call
arguments (ROADMAP P7 notes this as the current hardcoding case); `block_at` and `review_at` are
call arguments. A malformed answer fail-closes to `review`, not `block`. This is a triage of one
text you already hold, not a malware or phishing detector.

**Not for** deciding whether to trust content after reading it, or screening binary formats.

## jev_classify — many items, one catalog

**Use when** many items each belong to one class from a catalog you can write down (keep/drop,
routing, clutter/article), and you want one call.

**Measured.** L3 live run, 2026-09-23: n=6 items, selective_accuracy_auto 1.000, auto_coverage
1.000, macro_f1 1.000, micro_f1 1.000. Six synthetic items; no accuracy claim beyond them.

**Weak spots.** An item stands `auto` only when the top probability clears `auto_accept` (0.85) and
the margin over the runner-up clears `minimum_margin` (0.5). At those defaults the margin does not bind: two options whose probabilities sum to at most 1.01 already force a margin of at least 0.69
when the top is 0.85, so a close split cannot also clear `auto_accept`. The margin binds only if
you lower `auto_accept` below 0.755 or raise `minimum_margin` above 0.69. The same pair is the
default for compare and extract. Items and classes each have their own [caller ID](#caller-ids)
namespace. Item text cut at the cap is reported to telemetry only and does not change the action
(CONTEXT.md "Truncated Context" draws this line).

**Not for** labels you cannot enumerate, or when one item needs its own question — that is a batch
of noul or choice questions, not a catalog.

## jev_decide — one bounded choice

**Use when** one decision has between 2 and 6 options you can name, and declining is a real answer.

**Measured.** No recorded live eval. The scorer's primary metric is `overdecision_rate` (picking a
candidate when it should have escaped), with escape-hatch accuracy and requirement-check F1.

**Weak spots.** Escape hatches (`ask_user`, `investigate`, `none`) are enabled by default and
reserve those keys while enabled. Every candidate requires a [caller ID](#caller-ids).
Requirements are capped at 3, each up to 500 UTF-16 units — a decision with a longer rulebook
does not fit and belongs in code (ADR-0002), not in the question.

**Not for** open-ended planning, ranking, or anything with more than six options.

## jev_find — which candidate answers

**Use when** you have a set of candidate documents/notes and need the best one for a question, plus
whether any answers at all.

**Measured.** No recorded live eval. Scorer: recall@1, exists AUROC, MRR, NDCG@10, verdict
accuracy.

**Weak spots.** The exists verdict thresholds (answered at ≥ 0.7, absent below 0.35, else
`partial`) are frozen parity defaults, not call arguments. The semantic-find cookbook uses the same
numbers as examples to tune; this server does not (ADR-0001, `docs/reference/parity-manifest.json`). Candidate text is truncated at the cap
(telemetry only). `top_k` defaults to 5. Candidate IDs are [optional](#caller-ids).

**Not for** discovering candidates — it only ranks what you pass, unlike a search engine.

## jev_rerank — order candidates you already have

**Use when** search hits or grep results need a relevance order and every candidate should get a
score.

**Measured.** No recorded live eval. Scorer: NDCG@10 and Kendall τ against BM25 and original
order; an embeddings baseline is an open gap, not implemented (`evals/README.md`).

**Weak spots.** A flat band of low `relevance` values means the candidates were not
distinguishable; treat the order as weak (README § Operator notes). `ranked[].relevance` is the
probability at a fixed precision; there is no spread field. Aggregate candidate text over 100,000
UTF-16 units refuses with an `input_too_large` error. Candidate IDs are [optional](#caller-ids).

**Not for** deciding whether anything is relevant at all — that is `jev_find`'s exists question.

## jev_compare — two passages, one fact relation

**Use when** two passages should state the same fact and you need same / contradiction / different
facts, optionally per aspect (up to 10).

**Measured.** No recorded live eval. Scorer: contradiction recall on numeric/negation
perturbations, aspect-level F1.

**Weak spots.** Passages are schema-rejected above 20,000 UTF-16 units each — long documents must
be cut by the caller, and the cut decides what gets compared. When aspect verdicts contradict the
overall verdict, the result carries a warning rather than silently agreeing (ADR-0052).

**Not for** whole-document diffing or stylistic comparison; it judges fact relation, not text
difference.

## jev_extract — a value your regex proposes

**Use when** a value (version, date, price) sits in a document and your regex can propose the
candidates; Jev picks among them or returns null. Every field requires a [caller ID](#caller-ids).

**Measured.** No recorded live eval. Scorer: exact match, hallucinated values (must be 0),
not-found precision/recall.

**Weak spots.** The invariants are hard (ADR-0004, ADR-0018): the value is one of the verbatim
regex matches or null; a timing-out pattern returns `invalid_pattern`; a capped or too-long
candidate universe is never `auto` and never a definite `not_found`. The regex dialect is a narrow
subset of `re`; a pattern outside it refuses with `invalid_pattern` rather than misbehaving.

**Not for** values a regex cannot propose, or fuzzy matching — the universe of candidates is
exactly what your pattern matched.

## jev_review — is this patch acceptable

**Use when** a diff should be graded against its request before the task is called done.

**Measured.** No recorded live eval. Scorer: P(defective | auto), safe_to_apply AUROC,
severe-defect recall.

**Weak spots.** A confidence below `review_at` (default 0.5) requires review. Escalation requires a
named defect or safety refusal grounded in a changed line, or an invalid required judgment. A
malformed or missing answer to a requested defect-category question prevents `auto`. The rubrics are fixed
(correctness, spec_match, test_gap, blast_radius); callers cannot add one. Review checks any
supplied score distribution against the score. A string `diff` over
the cap is truncated and judgment over cut context never stands `auto`; a file-list `diff` is
reviewed per file instead — one oversized or unreviewable file lists in `unreviewed_files`, marks
the call `partial`, and keeps it off `auto` (ADR-0066).

**Not for** producing a rewritten patch, style review, or replacing the test run — it reads what
you pass (ADR-0063: diff and tests count as evidence when you do not pass them explicitly).

A held review includes `blockers` with the triggering field, observed value, applicable threshold,
and a next check. Rubric confidence blockers also show the score, nearest level and whether that
level is favorable, intermediate or adverse. Where a changed line is available, the next check
points to the patch. Caller-supplied test text is marked `tests_weight: self_reported`. The gate
omits that mark when the CLI or range tool reads the log itself.

## jev_gate — did the work finish

**Use when** a patch plus completion claims plus test logs should produce one ship decision with
per-claim verdicts. The completion check; call it before claiming done on a diff.

**Measured.** No recorded live eval. The design target is a false-AUTO rate accepted on its
Clopper-Pearson upper bound, not a point estimate (ROADMAP headline metrics); certifying the 0.5%
target needs roughly 600 error-free held-out rows per run, and that corpus is not recorded yet
(`evals/README.md` § Open gaps).

A confident contradiction with a cited counterexample escalates. A file list gets one review per
fitting file. First-fit grouping in file order packs patches into claim requests, each with all
claims, caller evidence, and tests. A confident cited contradiction takes precedence over
verification in any group. A confident verification in any group can stand if no group
contradicts it, and any invalid or missing answer holds that claim for review. A patch above
the packing estimate is sent in its own group. If the provider rejects a claims group for context
size, all files in that group appear in `skipped` as `oversize` and its claims require review.
Multi-group results and results with context rejections include `calls`, counting each review and
claim request. Usage sums the calls that report usage.

Evidence is capped at 16 items and 200,000 aggregate UTF-16 units. File lists allow up to 64 entries
and 200,000 UTF-16 units of patches in aggregate. The raw cap is 50,000 UTF-16 units per document
or file patch. Over-cap file patches are held for local review. Claims packing targets
30,000 compact UTF-8 bytes for state plus the longest question and 60,000 bytes total.
A string gate includes the diff and tests in review state and again as implicit evidence
(ADR-0063). The provider's token limits count both copies. Pass a file list when the string gate
exceeds the provider window.
[Caller evidence IDs](#caller-ids) reserve `none` for source selection and `diff` for patch
citations. They also reserve sanitized `diff:<path>` keys for file patches and `tests` when a log
is supplied.

**Reading a held gate.** Use `blockers` for file-by-file and claim-by-claim comparisons. For
example, `spec_match_confidence_below_review_at` can report a favorable level, confidence 0.49
and threshold 0.5. Check the requirement against its implementation. Summary reason codes use the
frozen vocabulary. Read detailed blocker codes in `blockers` and `rollup.reason_counts`. The
request, target patch and tests are the primary review inputs. The target path,
caller evidence items and sibling patches share a supplemental UTF-16 budget. When any of these
are omitted, `jev_review.context_omissions` or the gate’s `review.context_omissions` lists each
target path and index, required and allowed units, and omitted entries. This field is informational
on every action, including auto. Coverage flags and decisions still follow the primary inputs and
judgments. See [ADR-0081](adr/0081-gate-diagnostics-and-file-context.md) and [ADR-0086](adr/0086-shared-provider-window-and-gate-packing.md).

Read `rollup.action` for the whole change. `rollup.reason_counts` counts each detailed blocker
by code across files, withheld paths and claims. Accepted changes have an empty count map.
`review.file_rows` is ordered by input position and preserves repeated paths. The existing
`action`, `review.file_actions`, `score_file`, `reason_codes`, and numeric scores remain available.
`next_checks` starts with a prioritized action and also lists intent-to-add commands for untracked
files. Every other held comparison keeps its own check in
`blockers`. When a rubric needs review and a changed line is available, the suggested check
names a patch line to inspect. An omitted input on a held file is identified by path or evidence ID.
The source and defect category questions hold otherwise-auto decisions for review when their answers
are missing or malformed. Plain summaries require actual diff structure before they count as patches.

A named defect or explicit safety rejection cites a changed line. A confirmed claim contradiction
cites a counterexample and sets the claim excerpt to that line. The line number is one-based within
the submitted patch or evidence item. The source uses the canonical evidence ID sent to the
provider. Changed code remains citable even when it resembles a diff header.
`evidence_line` is always `{source, line, text}`. Invalid answers identify the provider question
in `invalid_answer`. Only a cited safety refusal sets `safety_refusal`. Invalid required answers
explicitly refuse a decision. Low confidence alone requires review. A provider
contradiction without sufficient corroboration yields `unsupported` and `reported_verdict:
contradicted`. Its probabilities still describe the provider's original judgment.
`citation_coverage` reports the number of eligible and offered lines. A held change can be split
by hunk to expose more lines. Aggregated token counts sum the reported usage across calls.
With `JEV_STUDY_USAGE_PROVENANCE=1` at startup, `usage.reported` is false if any contributing call
omitted its usage.
See [ADR-0082](adr/0082-actionable-gate-escalation.md).

From another git worktree run `jev-judge-mcp gate --diff BASE..HEAD --claims claims.txt
--tests test.log --request "requirement"` in that worktree. A range over the aggregate input budget returns `input_too_large` before a judgment. The same admission caps apply. The CLI reads claims and tests relative to the shell directory within the selected repository. Evidence uses the file judgment reader with gate unit caps, UTF-8 replacement, and typed refusal codes such as `path_outside_scope`, `not_found`, and `file_too_large`.
For changes exceeding the caps, separate concern-level decisions require an independent
cross-file review.

**Not for** a substitute for CI or tests — it judges the claims you pass against the evidence you
pass, and never runs anything itself.

## jev_score — your ordered rubric

**Use when** severity, quality, or risk should land on an ordered scale of 2 to 10 levels you
write, with the full per-level distribution.

**Measured.** No recorded live eval, and no bench corpus yet (extension tool, ADR-0048). Scorer:
nearest-level accuracy, level MAE, within-one rate.

**Weak spots.** Positions between levels are weakly calibrated: `score` is a probability-weighted
position, not a magnitude — threshold it against levels, do not interpolate (README tool table).
Levels and text bounds are owned by ADR-0048, not by a parity-manifest block. One call, one rubric:
several scales are several questions. A score that differs from the weighted mean of its reported
level probabilities by more than the combined rounding allowance returns `invalid_response`.
The allowance is half a hundredth times one plus the sum of the level indices, with floating-point
slack. The same check applies to score questions in `jev_file_judge`, `jev_files_judge`, and `jev_ask`.

**Not for** continuous magnitudes, or when you need an explanation — the answer is a distribution,
never text.

## jev_file_judge — judge a file without reading it

**Use when** a judgment lives in a file you have not read: does this config disable the timeout, is
this the file that names the retry policy, how careful does this generated code look. Name the path
and one question — noul, choice, or score — and the server reads the file as state and returns the
typed answer. The file's bytes never enter your context or the payload; write `instructions` against
the state's `content` field.

**Measured.** No recorded live eval, and no bench corpus yet (extension tool, ADR-0077).

**Weak spots.** The whole decoded file is the state, capped at 100,000 UTF-16 units; an over-cap or
binary file refuses typed (`file_too_large`; a NUL byte in the first 8,000 bytes means `binary_file`)
instead of being judged over a cut read. Known secret stores — every `*.env` dotenv and the dot-
prefixed `.env` family, `*.pem`,
`*.key`, `id_rsa`, `.npmrc`, and the like — refuse `secret_file` before any read (the `.example`/
`.sample`/`.template` stand-ins do read), and every read is credential-literal redacted before it
reaches the provider. Paths must resolve inside the server's working directory,
symlinks followed; there is no override, and an escaping path is refused `path_outside_scope` with no
provider call. One question per call: several files are `jev_files_judge`, the batched form of this
tool. Choice option IDs must contain 1 to 64 UTF-16 units each.

**Not for** exact lookups, counting, math, or anything grep answers — run the command and read the
answer yourself.

## jev_files_judge — the same judgment over many files

**Use when** one question spans a tree: which config in `config/` disables the timeout, how careful
is this generated module, does any file in the diff root still name the old flag. Name paths —
files, directories, or glob patterns — plus the same one question `jev_file_judge` takes, and the
server expands, prunes, and makes one provider call per surviving file. The payload reports
`{path, answer, status}` per judged file and `{path, reason}` per skipped one — a directory not
entered (`skipped_directory`), a secret store (`secret_file`), a binary or empty read, an over-cap
file (`too_large`), the hard cap (`over_the_file_cap`), a failed call (`call_failed:<code>`) — and
no file's bytes enter your context. Directories are walked to the leaves only with `recursive:
true`; dependency, build, cache, and VCS-internal directories are never entered either way. Picking
among the answers is `jev_find` fed those answers as candidates.

**Measured.** No recorded live eval, and no bench corpus yet (extension tool, ADR-0077).

**Weak spots.** The hard caps bound a call before the first provider call — at most 64 surviving
files from at most 32 path entries, each file at the `jev_file_judge` 100,000-unit read cap. The
same tree and paths always produce the same local skips. A provider context refusal fails the
batch with `input_too_large` and split advice. Other per-file call failures appear in `skipped`.
Read `skipped` before trusting `results`. File judgments and file-list reviews share a
runtime cap of eight simultaneous file judgments. With zero calls, the batch reports the requested
model and `provider: none`. If every call fails, the batch identifies the attempted provider.

**Not for** exact lookups, counting, math, or anything grep answers — run the command and read the
answer yourself.

## jev_ask — your questions over composed state

**Use when** several enumerable judgments share one state and no purpose-built tool fits: is this
test failure a real regression and how severe, does this diff carry a credential and which class of
change is it. Write the questions — noul, choice, or score, keyed by your ids — and compose the
state: your own framing in `state`, files the server reads in `paths` (the `jev_file_judge` rules),
and an optional `command` whose redacted output joins the state. An accepted command uses one
provider call for your questions, just like an ask without a command. Answers come back keyed by
your ids, fail-closed per question, with a state summary and usage. The provider receives generated
question keys such as `q0` in place of caller ids.

Command execution is off by default. Set `JEV_ASK_COMMANDS=1` in the server environment to enable it.
The server parses POSIX shell-style quoting into arguments and invokes the executable directly.
The fixed allowlist is `git status`, `git diff`, `git log`, `git show`, `ls`, `rg`, `grep`, `wc`,
`head`, and `tail`. There is no shell, expansion, pipeline, or redirection. Malformed quoting,
unquoted shell operators (including backticks and `$(`), executable paths, Git options before
the subcommand, and executables found inside the working directory tree are refused.

Each command has a **closed option allowlist**. Exact spellings are required. Any unlisted option
refuses with `command_refused`, the command's allowed options, and the final-block notice. A
disabled command returns `command_disabled` with the same notice. Both refusals happen before
execution or a provider call.

| Command | Flags | Options with required values | Options with optional attached values |
| --- | --- | --- | --- |
| `git status` | `-s`, `--short`, `--branch` | — | `--porcelain[=version]` |
| `git diff` | `-p`, `--name-only`, `--name-status`, `--no-color`, `--cached`, `--staged` | `-U`, `--unified` | `--stat[=width[,name-width[,count]]]` |
| `git log`, `git show` | `-p`, `--name-only`, `--name-status`, `--no-color`, `--oneline` | `-U`, `--unified`, `-n`, `--max-count`, `--since`, `--until`, `--author`, `--format`, `--pretty` | `--stat[=width[,name-width[,count]]]` |
| Both `rg` and `grep` | `-i`/`--ignore-case`, `-w`/`--word-regexp`, `-F`/`--fixed-strings`, `-n`/`--line-number`, `-c`/`--count`, `-l`/`--files-with-matches`, `-v`/`--invert-match`, `-x`/`--line-regexp` | `-A`/`--after-context`, `-B`/`--before-context`, `-C`/`--context`, `-e`/`--regexp`, `-f`/`--file`, `-m`/`--max-count` | — |
| `rg` only | `-s`/`--case-sensitive`, `-S`/`--smart-case`, `--hidden`, `--no-ignore` | `-g`/`--glob`, `--iglob`, `-t`/`--type`, `-T`/`--type-not` | — |
| `grep` only | `-r`, `-R`, `--recursive` | `--include`, `--exclude`, `--exclude-dir` | — |
| `ls` | `-l`, `-a`, `-A`, `-h`, `-R`, `-1`, `-t`, `-S` | — | — |
| `wc` | `-l`, `-w`, `-c`, `-m` | — | — |
| `head`, `tail` | — | `-n`, `-c` | — |

Required values can be attached or separate, as in `--max-count=5`, `--max-count 5`, `-n5`,
or `-n 5` where the option takes a value. Short flags can be bundled, as in `grep -in`.
A bare `--` ends option parsing and can delimit Git paths. The secret-store and private-directory
rules apply to every positional argument and option value.

Git runs with paging, fsmonitor, hooks, external diffs, and text conversion disabled, and ignores
system and global Git configuration. Configured worktree filters cause `git status` and `git diff`
to refuse.

**Measured.** No recorded live eval, and no bench corpus yet (extension tool, ADR-0077).

**Weak spots.** Nothing truncates. State and questions share a 120,000 UTF-16 unit cap.
Exceeding that cap or the provider's context window returns `input_too_large` with each part's
size and split advice. The split estimate includes JSON framing and question criteria.
Re-ask the same questions on each proposed call.
Own `state` is framing, capped at 20,000
units; command output is capped at 30,000 units and an over-cap run is killed and refused; the
command runs under a 30-second timeout, in the working directory, with closed stdin and a scrubbed
environment. Both output streams are redacted. Commands read with the server account's filesystem
permissions, including when traversing directories. 1 to 20 questions
per call, at most 20 paths. Choice option IDs use the same 1 to 64 UTF-16 unit limit as
`jev_file_judge`. Questions in one request cannot see each other's answers. Every
answer is typed: a probability, one option, or a scale position — never an explanation.

**Not for** exact lookups, counting, math, or anything grep answers — run that in your own shell
and read the result; `command` is for output you want judged, not output you want to read.

## jev_gate_range — did the work finish, read from git

**Use when** the change is in git and you would otherwise paste its diff into `jev_gate`. Name the
range (`main..HEAD`, or `HEAD` for staged and unstaged tracked changes against the last commit); each end must name a
commit or a tree. The server reads `git diff <range> -- .` in its working directory, so nothing
outside that directory is sent, and runs jev_gate's
handler on the file list, so the review sees the whole patch, never an excerpt you typed
(ADR-0080).

**Measured.** No recorded live eval; the judgment is jev_gate's.

**Weak spots.** Everything on the jev_gate card applies: the same caps, thresholds, fail-closed
paths, payload, and [caller evidence ID policy](#caller-ids). Only `tool` names this tool. A secret
store in the range (`.env`, keys) is never sent and is listed in `skipped` as `secret_file`.
Credential literals in other patches are redacted. Binary patches (including text marked `-diff`)
are listed as `binary`. A single revision also lists non-ignored untracked files as `untracked`.
For each one, run `git add -N <path>` and recheck. Any skipped file holds the range for local review. A range of only secret stores refuses
`secret_file`; a bad range, an empty diff, no
repository, or headers git quoted so they do not split refuse `invalid_arguments`; a diff past the
Git capture limit is killed mid-read and refuses `input_too_large`; all before any provider call. `tests_path` is read under jev_file_judge's path rules and is not self-reported. The working
directory is wherever the harness started the server: an entry that moves it
(`uv run --directory <checkout>`) reads that checkout from every project, so a range from your
project refuses; launch a checkout with `uv run --project <checkout>`, which keeps the working
directory. Git refusals name the server's working directory so the mismatch shows.

**Not for** a patch that is not in the working tree or its history — use jev_gate.
