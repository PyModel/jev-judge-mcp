---
status: accepted
---
# Published schemas are proven faithful to the argument validator at construction

The published `inputSchema` is the contract in two directions at once: it is what `tools/list`
shows the client, and it is what `tools/arguments.py` enforces with zod 3 semantics. Those two
roles stay in sync only if every schema the server publishes is one the emulator can faithfully
interpret. Until now that invariant was checked at call time — `_js_pattern` raised a bare
`ValueError` past the Toolset's owned error shapes, and an emulator-unknown construct (an `enum`,
a `oneOf`) would have been silently ignored: the client sees a constraint the server never
enforces, the quietest possible parity break.

At `Toolset` construction, every published schema is compiled once
(`compile_argument_schema` in `tools/arguments.py`) into an immutable parser, and runtime calls
that parser; patterns compile once, there. Each keyword is consumed by the node that enforces it,
so faithfulness is structural rather than a separate allowlist walk:

- **Keywords**: only the constructs a node enforces — `type`, `properties`, `required`,
  `additionalProperties: false` (enforced as zod's strip, matching the reference), `items`,
  `minItems`/`maxItems`, `minLength`/`maxLength`, `pattern` (must compile as an anchored ASCII JS
  pattern), `minimum`/`maximum`, `anyOf`, and the annotations `$schema`, `title`, `description`.
  A keyword left over after its node is built — one that node does not enforce, or any sibling
  beside `anyOf` — is a construction failure, never a silent ignore.
- **Combinations**: the root is an object with `properties`, and every refinement names one of its
  properties; `required` keys must exist in `properties`; an array needs `items`, a single schema
  (not draft-07 tuple form); `anyOf` is a non-empty list of non-union options of distinct types,
  so no option shadows another; lower length bounds are at least 1 and numeric bounds finite.
  Every subschema is compiled with a path-qualified violation.

A violation fails server construction with the tool name and schema path — a boot-time, named,
authored-data error, never a first-call crash and never a silently ignored constraint. The frozen
snapshot passes by construction; a future re-freeze that publishes anything the emulator cannot
reproduce fails loudly at startup instead of drifting at runtime.

## Consequences

- The invariant "every constraint a published schema advertises participates in parsing" has an
  enforcement point and a property test (`tests/property/test_argument_schema_properties.py`) that
  checks it over generated schemas and the real `TOOLS`.
- New keyword support (e.g. `enum`) is the compiler node that consumes and enforces it plus its
  witness in that property test, together.
- Construction failure is deterministic and needs no stderr telemetry beyond the exception text.

## Amendment (2026-09-22): the check is per-kind, and covers the root and refinements

The flat keyword allowlist let through schemas the argument validator does not honour. Every case
was reproduced, and none occurs in today's ten published schemas:
- `anyOf` with sibling keywords (the parser returns at `anyOf` and ignores the siblings);
- a keyword the node's type never reads, such as `string` + `minimum`;
- an array without `items`, which passes the check and then raises `KeyError` at call time;
- a non-object root;
- a `type` list, or a non-dict property, which crash the check instead of being rejected;
- a refinement keyed to a property that does not exist, which silently never runs.

Decision: each node accepts only its kind's keywords.

| Kind | Allowed keywords |
|---|---|
| `anyOf` | `anyOf` |
| `string` | `type`, `minLength`, `maxLength`, `pattern` |
| `number`, `integer` | `type`, `minimum`, `maximum` |
| `boolean` | `type` |
| `array` | `type`, `items` (required), `minItems`, `maxItems` |
| `object` | `type`, `properties`, `required`, `additionalProperties` |

It also requires:
- a Mapping node and a hashable `type`;
- a root with `type: "object"` and no `anyOf`;
- `Toolset` construction checks that every refinement key names a root property.

Landed (2026-09-22) as one step: the per-kind table and the compiled validator shipped together.
`compile_argument_schema` builds each schema once, in `Toolset` construction, into an immutable
parser whose nodes consume their own keywords, so the table is exactly what each node consumes and
a leftover keyword fails construction. The root must also carry `properties`. The zod issue order
is unchanged (the four -32602 fixtures are not re-recorded), `parse_arguments` stays as a thin
wrapper, and the consequence above reads as amended: new support is one node change plus its
witness in the property test.
