---
status: accepted
---
# The connecting agent can read the jev skill

A client that only speaks MCP never sees a checkout path. The routing skill already lived in docs. The `jev` skill teaches the Jev API, which is a different job. Both have to travel inside the installed package, and initialize has to say which is which.

## Decision

- `src/jev_judge_mcp/skills/jev/` is the owner's skill, packaged from https://github.com/PyModel/jev-skill at `ba5dd9f`. The allowlist is `SKILL.md`, `api-reference.md`, `patterns.md`, `prior-art/*.md`, and `LICENSE` (Copyright (c) 2026 elkaix). Nothing else under `src/jev_judge_mcp/skills/` ships or is served, except `jev-mcp/SKILL.md` and this package's `__init__.py`. `docs/reference/jev-skill/` stays gitignored.
- `docs/skills/jev-mcp/SKILL.md` is a symlink to the packaged routing skill. There is no `docs/skills/jev` symlink: a directory symlink makes Hatch record those bytes under `docs/` and drop them from the wheel.
- The server reads skill files only through `importlib.resources`. There is no checkout fallback.
- `initialize.instructions` names both skills and their resource URIs, and carries `ON_DEMAND` from this module's sibling `instructions.py`. No threshold numbers, no secrets, no harness names.
- The on-demand rule is: Jev is invoked when an unresolved judgment earns a model decision. Deterministic evidence takes precedence; Jev is not a mandatory ceremony. The same text names `jev_gate`, `jev_screen`, and `jev_verify`, and the skip cases. `docs/agent-rules.md` is the doc copy. Other docs point at it.
- This amends ADR-0061. `jev_gate` is the recommended final judgment before claiming done, not a required consult. Skip it when tests, type checks, build, lint, or another explicit acceptance criterion already settle completion.
- Pi's installer entry uses an allow-list (`directTools`) of the published tools plus `get_jev_mcp_skill_md`, so the rest of the packaged skill stays resources rather than tools (ADR-0036, amended here).
- `resources/list` and `prompts/list` serve the allowlist. Tool schemas, tool behavior, and policy are unchanged.
- The reference never captured initialize, and it served no skill resources. This is divergence `packaged-jev-skill`.

## Consequences

- `tests/contract/test_skill_resources.py` pins the stdio fetch and the exact on-demand sentence. `tests/integration/test_uvx_smoke.py` pins that a wheel built by `uvx --from .` still serves `jev` when the process cwd is not the checkout.
- Cookbook numbers on the official docs site are not this server's action defaults.
