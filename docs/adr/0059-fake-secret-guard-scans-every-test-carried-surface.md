---
status: accepted
---

# The fake-secret guard scans every repo-owned surface tests can carry a secret through

The unit-stage guard `tests/unit/test_fake_secret_lengths.py` was added after `eval` failed on
GitHub: a one-letter fake key in the runner's path collided with unrelated text (the guard's
failure class is a fake credential so short that redaction-style matching false-positives on it).
The first version scanned only `test_*.py`. A mutation audit (guard red under a reintroduced
failure) found the same class carried undetected by two surfaces the guard never looked at: a
short credential in `conftest.py` and one in a JSON fixture.

## Decision

- **One rule, every repo-owned surface.** The guard's rule — a non-empty credential-named string
  shorter than `MIN_SECRET_LENGTH` outside the exact exemptions — applies to every repo-owned
  surface tests can carry a secret through: `test_*.py`, `conftest.py` (one AST scan over both),
  and JSON/JSONL fixtures (credential-named keys with short string values). Recorded fixture
  content is output text, so a short fake there collides exactly like one in a test file.
- **Repo-owned means git's list, not the filesystem.** Both scans take their files from
  `git ls-files --cached --others --exclude-standard -- tests`: tracked files plus
  untracked-but-not-ignored ones, so a new test file not yet staged is still scanned, while a
  fetched gitignored tree — `make parity-reference`'s `tests/parity/reference/`, node_modules
  and all — never is (a third-party `{"key": "a"}` must not redden `make unit` locally; GitHub
  CI never sees that tree). Tracked files deleted from the working tree but not yet staged are
  skipped. A failed listing — or one that names nothing scannable — raises: the guard must be
  loud, never vacuously green.
- **Exact exemptions only.** A credential-shaped name that is not a credential is exempt by
  exact (path, surface, name, value), never by pattern: the floor-testing fixtures and Claude's
  recorded `apiKeySource: "none"` stream metadata (an enum, not a credential). A pattern-based
  exemption would silently admit the next short fake.
- **Malformed data is not this guard's failure.** A JSON line or document that does not parse is
  skipped; the fixture's own tests report malformed data.
- **New formats join the scan.** No YAML fixtures exist under tests/ yet; a fixture format that
  lands is added to the guard in the same commit (the rule the CI stages already follow: a new
  stage joins `ci` when its tests arrive).

The permanent self-tests cover the rule per surface: the synthetic short key in Python source
and in JSON, including the JSONL line form; and the listing contract in a throwaway git repo
with a hermetic environment (real git, no mocking): gitignored files are never listed, unstaged
new files always are — whatever their name's characters, which git would C-quote without `-z` —,
deleted-tracked files are skipped without erroring, and an empty listing raises.
