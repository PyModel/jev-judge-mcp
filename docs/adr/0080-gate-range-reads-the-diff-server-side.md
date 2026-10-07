---
status: accepted
---
# jev_gate_range reads the diff server-side

Agents hand-pack their diff into `jev_gate.diff`: an excerpt, a summary, or a paraphrase. The
review then scores what it was given — an incomplete patch — low, and the gate escalates on a
change that may be fine. The `jev-judge-mcp gate` CLI (ADR-0064) never had the problem: it reads
the range from git itself and builds the `[{path, patch}]` file list (ADR-0066). This ADR puts the
same read behind an MCP extension tool, the way ADR-0048 opened the extension path: appended after
the last extension, with its own pinning tests, and nothing in a frozen tool changes.

## Decision

- **`jev_gate_range` is jev_gate with the diff read by the server.** Arguments: `request`,
  `range`, `claims`, `evidence`, optional `tests_path`, and optional `auto_accept`, `review_at`,
  `composite_floor`. Every argument it shares with jev_gate is jev_gate's own published schema
  fragment, imported, not copied, and so is the evidence refinement; `evidence` stays required
  because jev_gate's handler requires it. The handler builds jev_gate's arguments and calls
  jev_gate's handler: the review, the claim verification, the caps (`limits.GATE`), the
  thresholds, the fail-closed paths, and the payload are jev_gate's. The only change to the result
  is its `tool` field, which names `jev_gate_range`, on a success and on the isError budget
  refusal alike.
- **One boundary reads git.** `git_diff.py` owns the repository lookup (`repo_root`), the range
  check (empty, a leading `-`, or a NUL refuses), `git diff <range>`, and the `diff --git` splitter
  with its header rules; `diff_argument` returns the file list, or the raw text when a header does
  not split, exactly as the CLI always sent it. `cli.py` and the tool import it, and each wraps its
  `GitDiffError` in its own refusal shape: the CLI's `invalid_arguments` DecisionResult with exit
  2, unchanged, and the tool's typed `invalid_arguments` ToolError. The message text is the CLI's,
  verbatim. Core (`domain/`, `validation/`, `policy/`, `limits`, `text`, `serialize`) does not
  import it.
- **Every git call is fixed argv, `git -C <dir>`, no shell, and bounded.** `GIT_TIMEOUT_SECONDS`
  (30) is the same scale as ADR-0077's command timeout, owned here; a timeout, a missing git, or output that does not decode is
  the same refusal as a failed git call. The bound is a process bound, not an input cap, so it
  lives beside the call and not in `limits.py`. git's output is untrusted text: it reaches the
  provider only as jev_gate's diff, under jev_gate's caps and anti-injection framing.
- **git's output is canonical, whatever the operator's config.** The diff runs with
  `--no-color --no-ext-diff --no-textconv --no-renames --src-prefix=a/ --dst-prefix=b/`: forced
  color would put escape codes in the patch, `diff.noprefix` would defeat the header split, an
  external driver or textconv filter would replace the patch or run a program, and rename
  detection would show a renamed secret store's old lines under its new name. The CLI shares them.
- **Each end of the range names a commit or a tree.** Every end of `A..B`, `A...B`, or a single
  revision is resolved, then peeled to a tree; a blob end refuses `refusing that git range` before
  `git diff` runs. A blob diff's header carries an object id, not a file name, so no path guard
  could see that it holds a `.env`. An unknown revision refuses with the same text.
- **The read is bounded and off the event loop.** git's stdout is read only up to three bytes per
  unit of `limits.GATE.aggregate_evidence_units` (a UTF-8 bound that cannot under-count UTF-16),
  and git is killed past it; the tool refuses `input_too_large`. The tool runs the read in a worker
  thread, so a slow git blocks no other call.
- **The diff stays inside the working directory.** The tool runs git in the working directory
  with the `.` pathspec, so a server launched in a subdirectory never sends the rest of the
  repository: the same scope the file tools keep (ADR-0077).
- **Server-read patches get the file tools' read guards.** A patch whose path is a secret store
  (`file_state.is_secret_store`) never leaves; it is listed in the payload's `skipped` as
  `secret_file`, the files_judge reason. Every other patch passes through
  `redact_credential_literals`. A range of only secret stores refuses `secret_file`; a diff whose
  headers do not split refuses `invalid_arguments`, since no per-file guard can apply. The CLI is
  run by the operator over their own repository and keeps sending what git prints.
- **The repository is the one containing the server's working directory.** The launch directory
  is already the operator's scope decision (ADR-0077); there is no repository argument a prompt
  could argue for, and the pathspec above keeps the diff inside it.
- **The test log is read by the file tools' reader.** `tests_path` goes through
  `file_state.resolve_scoped` and `read_state`: inside the working directory with symlinks
  followed, no secret stores, no binary files, the per-file size cap, and credential-literal
  redaction. Its refusals keep their own typed codes (`path_outside_scope`, `not_found`,
  `secret_file`, …); a bad range, an empty diff, or no repository is `invalid_arguments`. Both
  refuse before any provider call.
- **The server read the log, so it hashes it.** ADR-0067 sets `tests_sha256` only when a reader
  read the file; this tool is that reader. The hash is of the text sent to the provider — the
  log after redaction — so it names what was judged. A log read this way is not
  `tests_weight: self_reported`.
- **Registration.** Published after `jev_files_judge`. One divergence entry
  (`gate-range-tool-extension`, surface `tool_schema`, the `score-tool-extension` fields). No new
  caps and no `docs/reference/limits.md` section: the tool's bounds are `limits.GATE`. No new
  answer path, so `test_fail_closed.py` gains no rows; jev_gate's rows cover the handler both
  tools run. The CLI's DecisionResult reads the payload's top-level `action`, as for jev_gate.

## Considered Options

- **Guidance alone: tell agents to paste the whole diff** — rejected: an agent packs what fits
  its context, and every packed copy costs that context; the server can read the range for free.
- **A `range` argument on jev_gate** — rejected: jev_gate is a frozen snapshot tool (ADR-0048);
  its schema does not change.
- **A second copy of the gate in the tool** — rejected: two gates drift (ADR-0078).
- **A repository argument** — rejected for the same reason ADR-0077 has no `allow_outside_cwd`.

## Consequences

- An agent with a committed or staged change names the range and gets the whole patch reviewed;
  a patch outside git still goes through jev_gate.
- The CLI's gate output is unchanged under a default git config; under forced color, no-prefix,
  or an external driver it now reads the plain patch. Its git calls gain the timeout, and a git that cannot run,
  hangs, or prints undecodable output is now the matching refusal (`gate refuses to run outside a
  git repo` for the lookup, `git diff failed` for the diff) instead of a traceback or a hang.
- The security corpus gains one `ToolCase`; its range is git's empty tree against a committed
  fixture directory, so it resolves in a shallow clone.
