# ADR-0089 — The gate says who settles a held row

Status: Accepted  
Date: 2026-11-20

## Context

`review` means the caller still owns the row. The agent rules told callers to confirm a `review`
row "with tests, source reading, or a stronger check", and several gate checks read as requests to
ask Jev again ("Obtain a valid provider judgment", "Check this exact claim against a direct
observation"). An agent told to act on anything that is not `auto` read Jev as the stronger check
and re-called the gate with unchanged input. An unchanged call returns the same judgment, and the
optional response cache (ADR-0047) replays it byte for byte, so the agent looped on `review`.

The thresholds and actions are correct and stay strict. What was missing is which party can
settle a held row: a new call with more input, or the caller's own check.

## Decision

Every gate and review blocker carries `next_call`, set where the blocker is made:

- `with_input`: one more call that adds the input its `next_check` names can move the row. It is
  set for input over budget, an unsupported claim, an unconfirmed contradiction, a missing or
  caller-note-only source, a file the provider refused as too large, and a withheld untracked or
  oversize file.
- `none`: no call settles it. The caller settles it with its own check. It is set for rubric,
  safe-to-apply and composite thresholds, low claim confidence, a cited contradiction or defect,
  a missing defect line, omitted file context, and every invalid provider answer.

`jev_gate` adds `rollup.next_call`: `with_input` when any blocker is `with_input`, when the
emitted check is the re-pack check for a diff with no hunks or a hand-trimmed excerpt, or when
the provider refused a claim group for context overflow; otherwise `none`. The agent rules, the
server instructions, the routing skill and the gate description say: settle `review` with your
own check, and call again only for `with_input`, once, adding the named input. A row that holds
after that call is the caller's to settle.

Invalid answers are `none`, not a retry. The server is stateless, so "retry once" could only live
in prose, and the cache can replay the invalid answer.

`next_call` never changes `action`, `reason_codes`, thresholds or any judgment. `jev_review`
blockers carry it without a rollup. `jev_verify`, `jev_classify` and `jev_compare` return
`review` without blockers; for them the instruction sentence is the whole change.

## Consequences

- An agent following the rules makes at most one more call per held gate and then stops calling.
- Several checks are reworded as caller checks. The tests pin the tag of every blocker code and
  that a `none` check never asks for another call.
- The parity harness checks the rollup's `next_call` against the blockers and the emitted check.
- Copies of the agent rules outside this repository must be updated by their owners.
