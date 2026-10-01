---
status: accepted
---
# The hook gate asks guard questions, not one bare allow

`hook gate` judged every event with one generic allow/deny Choice. That flattened two different
judgments into one: what a shell command would do to the working directory, and whether a written
file carries material that must never leave the machine. One question also had to carry both the
observation and the verdict, so its reason could only say "denied" without naming what failed.

## Decision

- **Routing.** The event's tool name routes the judgment. `Bash` gets two questions in one
  provider call: an effect Choice (`read_only` / `reversible` / `irreversible`) and a
  destructive-intent Noul. `Write` and `Edit` get two checks computed in code — repo containment
  and a credential-literal scan — and, for content that survives both, one credential-in-content
  Noul. Any other tool name keeps the generic allow/deny Choice, so an operator who widens the
  matcher still gets judged.
- **Per-question thresholds.** Every question's boundary is a hook constant in
  `src/jev_judge_mcp/hook.py`, documented there and never in `policy/thresholds.py` (ADR-0035).
  The effect question keeps the existing floors: reported confidence escalates below 0.5, a
  margin estimate below 0.4. The destructive-intent Noul denies at or above
  `DESTRUCTIVE_INTENT_THRESHOLD` (0.7); the credential Noul denies at or above
  `SECRET_CONTENT_THRESHOLD` (0.7). At the threshold acts, matching the floors' convention.
- **Policy order, Bash.** A missing answer is `unreachable`; a malformed one is `unsure`; both
  ask. The destructive-intent veto then fires even over an unsure effect. Below the floors the
  effect asks. A confident `irreversible` denies. Everything else abstains silently.
- **Containment before any call.** For a Write or Edit, the target path is resolved against the
  event's working directory with `realpath` (user expansion, relative join, symlink resolution).
  `outside` denies immediately: no settings load, no provider construction, no provider call.
  `unknown` (no usable path or working directory) does not deny; the state says the path could
  not be checked and the credential question still runs. The computed verdict is stated in the
  state as evidence, so the provider judges over it instead of guessing.
- **Reasons.** A deny reason names the failure and its confidence or probability — irreversible,
  destruction as the intent, a credential in the content, or a write outside the working
  directory — and ends with the final-block notice in `hook_render.FINAL_BLOCK_NOTICE`, which
  tells the agent the block is final and not to retry, split, or reroute the action. The notice
  is pinned verbatim at the render boundary.
- **Unchanged contracts.** `permissionDecision` is still `deny` or `ask`, never `allow`
  (ADR-0035). The fail-open and fail-ask matrix is untouched (ADR-0065). No new environment
  variable. The hook still logs neither the action nor the state, and still passes one 30 second
  budget to its single provider call.
- **Credential literals are evidence; code is not.** A false positive denies a normal write
  with no recourse, so the scan is strict and high precision by construction: the detector in
  `src/jev_judge_mcp/credential_literal.py` hits only well-known token formats (AWS access key
  ids, GitHub and Slack tokens, OpenAI/Anthropic-style `sk-` keys with twenty mixed-case
  characters and a digit after the prefix so a CSS class name never matches, PEM private-key
  blocks, JWTs) and a quoted literal that is long, whitespace-free, and carries lower case,
  upper case, and a digit, assigned to a key name that ENDS with the secret word (`secret_key`,
  `api_key`, `DB_PASSWORD`, `auth_token` — never `password_help`, `token_label`, or a bare
  `auth` stem). A bare identifier, a type annotation, a function name, a reference such as
  `os.environ[...]` or `settings.x`, an f-string placeholder, and a low-variety placeholder
  such as `CHANGE-ME-IN-PRODUCTION` are never a hit; low-variety values stay with the credential
  question. Both directions are pinned as table tests at the detector's own boundary. A hit
  denies outright — no provider construction, no provider call — because the raw secret must
  never leave the process and the judge must never see it even redacted.
- **The write path is judged as code.** `redact_action` is a shell-command redactor: it rewrites
  ordinary code (`api_key = os.environ[...]`, `password: str = field(...)`), which would corrupt
  the very text the credential question judges. Write/Edit state is therefore redacted only by
  the strict detector — an identity on any content that passed it — and `redact_action` stays on
  the Bash and generic paths (ADR-0034).

## Consequences

- An outside-of-repo write, and an in-repo one carrying a credential literal, cost no provider
  call and cannot hang on a missing key: the deterministic denials happen first. With
  `JEV_HOOK_REQUIRED=1` they still deny, because a deny is stronger than the ask the flag would
  have produced.
- A routine, confident, non-destructive command abstains with no output, exactly as before; the
  decomposition changes what is denied, not what is allowed, and the hook still never allows.
- The generic fallback's deny reason now carries the failure phrase and the final-block notice,
  so every deny from this hook reads the same way.
- Questions, criteria wording, thresholds, and the notice live in `hook.py` and
  `hook_render.py`. Changing any of them is a hook change, guarded by the hook tests at the
  judge boundary.
