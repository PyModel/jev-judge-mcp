---
status: accepted
---
# One registry, one execution kernel, ten declarative tool definitions

P5 must publish the frozen `mcp.types.Tool` objects (ADR-0010) and route `tools/call` to them. The SDK offers no path for that pair: `ToolManager.add_tool` builds tools through `Tool.from_function`, deriving `inputSchema` from the Python signature (verified in the mcp 2.2.0 wheel, `tool_manager.py:39-67`), which cannot reproduce the snapshot. The SDK does offer both halves we need: `MCPServer._handle_list_tools` and `_handle_call_tool` delegate to the overridable `list_tools()`/`call_tool()`, so `JevMCPServer` owns discovery and dispatch without `_tool_manager` and without dropping to the low-level `Server`.

The shape is one registry, one kernel, ten definitions — not ten self-executing tool modules. `tools/toolset.py`'s `Toolset` is the single registry: the name→`JevTool` map, `tools/call` dispatch, and the reference's three `isError` shapes. `tools/base.py`'s `Runtime` is the kernel that executes the pipeline: validate args → enforce Caps → build State + Questions → `Provider.evaluate` → Policy → serialize. Each tool module contributes one declarative `JevTool` (`tools/__init__.py`, registration in snapshot order) plus its frozen constants (instruction strings, criteria, framing text, payload key order) and never runs the pipeline.

The registry's invariant: **published tool == callable tool == the same definition**. There is no list registry separate from the dispatch registry, and a test asserts `set(published names) == set(callable names)`.

Error semantics are owned, not inherited. Nothing an agent sees comes from pydantic, the MCP SDK's validators, or an accidental exception: argument rejections render through `tools/arguments.py` with the frozen zod-compatible messages (the four untagged `MCP error -32602` fixtures), and provider/handler errors render through the Toolset's three result shapes.

Provider lifecycle sits in the `Runtime`: the Provider is resolved on the first question — a call that never asks (jev_extract with zero candidates) never fails on configuration, as the reference resolves inside `askJev` (`provider.ts:104-106`). Success is cached and closed exactly once on shutdown; a failed resolution is **not** cached — the assignment never happens, so the next call that asks resolves again. With frozen Settings the text is identical every time, keeping the 18 resolver-error fixtures byte-identical without poisoning the cache against a later retry.

Import direction, generalizing ADR-0002's one-way layers: `policy/`, `limits.py`, `text.py`, `serialize.py`, and `extract/dialect.py` never import `tools/` or `server.py`.

## Considered Options

- **Ten tool modules that each execute the five-stage pipeline** — rejected: ten places would know execution order, provider invocation, error behavior, and result construction; concentrating that knowledge is the point of the design.
- **Low-level `mcp.server.Server`** — rejected as unnecessary: the SDK recommends it for exact hand-authored schemas, but overriding the two delegating methods gets the same wire behavior with the high-level server's transport and lifecycle intact.
- **Per-call provider resolution** — rejected: `resolve_provider` constructs a fresh Provider and `httpx.AsyncClient` each call, violating P9's p50 < 5 ms local-overhead budget and leaving `aclose()` unowned.

## Consequences

- `JevMCPServer` grows a `call_tool` override and the registry; nothing else in `server.py` changes.
- Malformed arguments cause zero provider calls; kernel ordering is test-enforced.
- The unknown-tool result is deterministic and parity-compatible.
- P5 lands as separated PRs: registry/runtime → Caps + fixture loader → judgment tools → extract, each independently revertable.

## Amendment (2026-09-22): the kernel as built, and one payload frame

The text above calls `Runtime` "the kernel that executes the pipeline" and says tool modules
"never run the pipeline". The code has always split the work three ways, and this amendment makes
that split the record:

- **`Toolset`** owns discovery, dispatch, argument validation (`tools/arguments.py`), serialization,
  and the three `isError` shapes.
- **`Runtime`** owns Provider access (resolution on first ask, cached success, `aclose`), the
  regex executor, and telemetry.
- **Each handler** owns its stages in a fixed order: Caps → State + Questions → `Runtime.ask` →
  Answer Validation → Policy → payload body.

The registry invariant (published tool == callable tool) and the import direction are unchanged.

Decision (implemented 2026-09-22):
- **Payload frame.** Every one of the 13 success payloads opens with exactly `tool, model, provider`
  and closes with exactly `usage`. A `frame(tool, evaluation | None, body, *, model=None)` helper
  in `tools/base.py` owns that head and tail, including the no-ask shape (`provider: "none"`,
  `usage: null`, which `jev_extract` uses when no candidate matched). The body keeps its insertion
  order. The gate refusal (`{tool, error}`, `isError`) stays outside the helper.
- **Rejected:** having handlers return body plus judgments for the Runtime to frame. It needs a sum
  type for the refusal and no-ask paths and changes all ten handler interfaces, while hiding the
  whole payload from the reader.
- **Telemetry `actions` means one headline Action per call**, `ToolResult.action: Action | None`.
  Today the count mixes per-item (classify, verify, extract) and per-call (compare, gate, review)
  units and is empty for four tools. Per-item counts, if wanted, go in a separate `item_actions`
  metric. Screen's `review` counts as its headline; compare's per-aspect decisions do not. This
  closes P9 gap G2. As built: verify, classify and extract headline their worst item Action and
  fill `item_actions{tool,action}`; a call whose rows carry no Action (extract all `not_found`)
  has no headline.
- Tests: `frame` key order for both paths; a structural head/tail check across all ten tools; one
  headline action per call per tool. The byte-equal tool fixture replay stays the regression lock.

## Amendment (2026-09-24): eleven definitions — the frozen ten plus one extension

The single registry and kernel are unchanged, and the snapshot ten stay byte-identical and first
in `tools/list`. The published surface may grow beyond the snapshot when the addition is a
registered divergence with its own ADR, caps, and pinning tests; the extensions register after the
snapshot ten, in their own order. First extension: `jev_score` (ADR-0048).


## Amendment (2026-09-25): argument descriptions state item shapes

The snapshot ten stay byte-identical in name, title, description, execution, and every schema
constraint. One text-level divergence is now sanctioned
(`docs/reference/divergences.json`, `argument-item-shape-descriptions`): the description of
every array-typed argument states its item shape plainly — claims, requirements, aspects, and
rubric levels are plain strings; evidence items, classify items and classes, decide and
find/rerank candidates, extract fields, and review/gate file lists are objects with named keys.
The 2026-09-25 Claude dogfood mis-shaped four first calls by pattern-matching from other jev
surfaces; the schemas already rejected them with the typed `invalid_arguments` error, and the
descriptions now say the shape before that rejection. No constraint changes: the `items`
schemas are untouched, `jev_screen` stays byte-pinned, and
`tests/contract/test_tools_list.py` keeps pinning name, title, and execution for the affected
tools (`SCHEMA_DIVERGENCE`).
