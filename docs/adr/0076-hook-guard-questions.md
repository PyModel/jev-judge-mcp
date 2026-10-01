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
  destructive-intent Noul. `Write` and `Edit` get a deterministic repo-containment check computed
  in code, a deterministic credential-pattern check on the written content, and — for content
  that survives both — one credential-in-content Noul. Any other tool name keeps
  the generic allow/deny Choice, so an operator who widens the matcher still gets judged.
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
- **Redaction order.** `redact_action` runs on the action input before the provider sees it
  (ADR-0034), and a raw secret never leaves the process. That redaction is also evidence: for a
  judged Write or Edit (containment inside or unknown), input the redactor would change denies
  outright — "the written content matches a credential pattern", no measure, the final-block
  notice, and no provider construction. A credential question that judged a `[redacted]`
  placeholder would be a judgment over erased evidence. Only content the redactor leaves
  unchanged reaches the credential Noul, which then covers the shapes the patterns miss.

## Consequences

- An outside-of-repo write, and an in-repo one whose content matches a credential pattern, cost
  no provider call and cannot hang on a missing key: the deterministic denials happen first. With
  `JEV_HOOK_REQUIRED=1` they still deny, because a deny is stronger than the ask the flag would
  have produced.
- A routine, confident, non-destructive command abstains with no output, exactly as before; the
  decomposition changes what is denied, not what is allowed, and the hook still never allows.
- The generic fallback's deny reason now carries the failure phrase and the final-block notice,
  so every deny from this hook reads the same way.
- Questions, criteria wording, thresholds, and the notice live in `hook.py` and
  `hook_render.py`. Changing any of them is a hook change, guarded by the hook tests at the
  judge boundary.
