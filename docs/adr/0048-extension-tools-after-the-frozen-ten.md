---
status: accepted
---
# Extension tools are published after the frozen ten, registered as divergences

Amends ADR-0013: the registry still has one kernel and one definition per tool, and the snapshot
ten stay byte-identical on the wire — but the published surface may grow *beyond* the snapshot, one
tool at a time, when the addition is a registered divergence with an ADR, its own caps, and its own
pinning tests. The first extension is `jev_score`.

## Decision

- **Order.** `tools/__init__.py` registers the snapshot ten in `tools_list_order`, then the
  extensions in their own order. `tools/list` serves the snapshot as a prefix, so a client written
  against the reference sees exactly the reference surface first.
- **jev_score.** Grades a caller-supplied subject on an ordered rubric of 2–10 levels (the
  documented score question space, `docs/jev_docs/primitives.md`) and returns the fractional
  0-based level index, the nearest level, the full per-level distribution, and confidence. The
  score question type was already first-class in the domain; this tool hands the rubric to the
  caller instead of fixing it, as `jev_review` does.
- **Answer validation is strict and whole.** `validate_rubric_answer` requires the shared score
  validator to pass, the score to land inside the rubric (`0..n-1`; the shared validator's
  `[0, 2]` window is wider than a short rubric), and the distribution to carry exactly the keys
  `"0".."n-1"` with finite probabilities summing to 1 within the shared tolerance. A malformed
  component rejects the whole answer (`invalid_response`): an extension tool has no
  partial-answer shape to project, and no legacy behavior to preserve. A malformed confidence
  stays `None` beside a valid judgment (ADR-0043), exactly as for `jev_review`'s rubric scores.
- **No policy thresholds.** `jev_score` decides nothing: there is no Action, no auto tier, no
  escalation. Thresholding the score is the caller's code (the docs' own advice: threshold, don't
  reconstruct a magnitude). Nothing joins `policy/thresholds.py`.
- **Caps.** `limits.SCORE` owns every value (2–10 levels, 1–200 units per level, 1–1500 subject,
  12 000 context); there is no parity-manifest block for an extension, so the ADR is the source
  and `tests/contract/test_limits.py` ties the values to the published schema (the `FindCaps`
  precedent for manifest-absent caps).
- **Registration.** One divergence entry (`score-tool-extension`, surface `tool_schema`) covers
  the surface growth. Every count-pinned test that assumed exactly ten tools re-pins to the
  published set: `tools/list` order (snapshot prefix + extensions), the install handshake's
  expected names, the doctor allow rules, the routing skill's tool census, and the fixture-corpus
  pin stays scoped to the reference ten (an extension has no reference recording; its pinning
  tests are its own).

## Considered Options

- **No extensions, rubric grading stays inside jev_review** — rejected: a caller-supplied rubric
  is a distinct capability, and hiding it behind four fixed rubrics serves no parity interest (the
  reference's own surface is not the product's ceiling once the divergence is registered).
- **A separate Toolset for extensions** — rejected: two registries can drift from one another and
  from `tools/call`; ADR-0013's single-registry invariant is worth keeping intact.
- **Publishing extensions before the snapshot tools** — rejected: a prefix keeps
  reference-authored clients working without bookkeeping.

## Consequences

- An extension tool never edits a snapshot tool's schema, output, or fixtures; it only appends.
- The fail-closed matrix gains one row set per extension answer path (`test_fail_closed.py`), and
  the security corpus gains one `ToolCase` per extension (`tests/security/tools.py`).
- Future extensions repeat this pattern: ADR + divergence entry + `limits` caps + pinning tests,
  appended after the previous ones.
