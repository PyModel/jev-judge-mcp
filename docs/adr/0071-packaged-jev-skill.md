---
status: accepted
---
# The connecting agent can read the jev skill

A client that only speaks MCP never sees `docs/skills/`. The routing skill already lived there. The `jev` skill (the renamed MIT copy of `aaddrick/building-with-typesafe-jev` at `c1802b24bb94`) teaches the Jev API, which is a different job. Both have to travel inside the installed package, and initialize has to say which is which, or a `uvx` client cannot tell them apart.

## Decision

- The verbatim `jev` skill, its MIT `LICENSE`, and a provenance note live in `src/jev_judge_mcp/skills/jev/`. The routing skill's text lives in `src/jev_judge_mcp/skills/jev-mcp/SKILL.md`. Checkout paths `docs/skills/jev` and `docs/skills/jev-mcp/SKILL.md` are symlinks to those files. `docs/reference/jev-skill/` stays gitignored so an untracked reference copy cannot collide with this tree.
- The server reads those files only through `importlib.resources`. There is no checkout fallback.
- `initialize.instructions` stays the short registry string from ADR-0061, plus which skill is which and the resource URIs. No threshold numbers, no secrets, no harness names.
- Both roles use one sentence: Jev is on demand only; call it when an independent judgment materially improves the decision; never route every judgment through it. The same words name `jev_gate`, `jev_screen`, and `jev_verify` as the high-value calls, and name the skip cases. The verbatim `jev` body is not edited; the connecting role (instructions, resource description, provenance) carries the sentence. The `jev_gate` ship check in ADR-0061 stays a named step, not a consult on every judgment.
- `resources/list` and `resources/read` serve every file in those two directories. `prompts/get` of `jev` and `jev-mcp` returns that skill's `SKILL.md`. Tool schemas, tool behavior, and policy are unchanged.
- The reference never captured initialize, and it served no skill resources. This is divergence `packaged-jev-skill`.
- Installer, setup, and doctor do not mention the routing skill today, so they gain no new line. README, the harness samples, the caller guide, the tool cards, and the agent rule block do, because those are the paths that already deliver `jev-mcp`.

## Consequences

- `tests/contract/test_skill_resources.py` pins the stdio fetch. `tests/integration/test_uvx_smoke.py` pins that a wheel built by `uvx --from .` still serves `jev` when the process cwd is not the checkout.
- An agent that copies cookbook thresholds from `jev` onto these tools is wrong. The provenance note is the list, and the instructions point at it.
