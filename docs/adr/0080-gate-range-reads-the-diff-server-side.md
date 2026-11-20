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
  thresholds, and the fail-closed paths are jev_gate's. The result names `jev_gate_range` in
  its `tool` field and includes `skipped` when the reader withholds part of the range.
- **One boundary reads git.** The MCP tool and the CLI call `gate_range.read_range`.
  It uses `git_diff.py` to find the repository, pin the endpoints, read the diff and split its
  file headers. If the headers cannot be split, both callers refuse with `invalid_arguments`
  before sending any patch to the provider.
- **Every git call is fixed argv, `git -C <dir>`, no shell, and bounded.** `GIT_TIMEOUT_SECONDS`
  (30) is the same scale as ADR-0077's command timeout, owned here; a timeout, a missing git, or output that does not decode is
  the same refusal as a failed git call. The bound is a process bound, not an input cap, so it
  lives beside the call and not in `limits.py`. git's output is untrusted text: it reaches the
  provider only as jev_gate's diff, under jev_gate's caps and anti-injection framing.
- **git's output is canonical, whatever the operator's config.** The diff runs with
  `--no-color --no-relative --submodule=short --no-ext-diff --no-textconv --no-renames`
  and `--src-prefix=a/ --dst-prefix=b/`. Paths are repository-relative and submodules use the
  short format. Fixed prefixes keep the file headers parseable. Disabling color, external drivers,
  and text conversion preserves the plain patch. Disabling rename detection keeps the old
  path of a renamed secret store visible to the reader.
- **Each endpoint must establish where its files came from.** The reader pins revisions to
  object IDs before reading either diff. A commit supplies the repository root. A subtree
  named as `commit:path` supplies its original directory. A bare nonempty tree ID supplies
  neither and is refused. The empty tree ID is accepted because it contributes no paths.
  A single revision compares to the working tree. Two-dot ranges accept these same endpoints.
  Three-dot ranges require commits, and omitted range endpoints default to `HEAD`.
  Blobs and unknown revisions yield `invalid_arguments`.
- **The read is bounded and off the event loop.** git's stdout is read only up to three bytes per
  unit of `limits.GATE.aggregate_evidence_units` (a UTF-8 bound that cannot under-count UTF-16),
  for each of the ordinary and full-context diffs. If either exceeds this bound, the reader
  stops git and refuses with `input_too_large`. The MCP tool runs the read in a worker thread.
- **The MCP working directory and CLI repository root define their respective scopes.** Both
  readers pass the `.` pathspec and check each printed path against every possible repository
  origin. An origin outside the caller's scope yields `invalid_arguments`. The MCP scope matches
  the file tools (ADR-0077). The CLI reads from the repository root even when invoked below it.
- **Both callers inspect the full text diff before sending a patch.** The reader matches paths and
  full-index headers between the ordinary and full-context diffs. A mismatch yields
  `invalid_arguments`. It withholds the file if any possible path is a secret store, its displayed
  name contains a recognized credential, or either side contains a beginning or ending PEM
  private-key delimiter anywhere in the file. For permitted files it redacts credential
  literals and sends only the ordinary patch.
- **Withholding changes the outcome.** For a mixed range the MCP tool reviews the permitted
  files, lists withheld paths in `skipped` with reason `secret_file`, and holds the range for
  local review. Sensitive path labels are redacted. If every file is withheld, the tool refuses
  with `secret_file` before a provider call. The CLI refuses with `secret_file` and exit 2 before
  calling the gate if even one file is withheld.
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

- An agent with a change in tracked files can name the range and have the server read the
  eligible patches directly. A patch outside git still goes through jev_gate.
- The CLI reads the guarded plain diff even under forced color, no-prefix, or an external
  driver. Git timeouts and read failures produce a local refusal.
- The security corpus gains one `ToolCase`; its range is git's empty tree against a committed
  fixture directory, so it resolves in a shallow clone.

## Amendment (2026-10-19): git refusals name the server's working directory

The repository is the one containing the server's working directory, and the harness, not the
caller, picks that directory. An entry launched with `uv run --directory <checkout>` therefore
reads the checkout from every project, and the caller saw only `refusing that git range`. The
tool's git refusals (`invalid_arguments`) now end with `(server working directory: <path>)`, so
the caller can tell a wrong range from a server started outside its project. The CLI omits
that suffix because it runs in the operator's own shell.
The path names the local process's directory and reaches only the client, never the provider.
