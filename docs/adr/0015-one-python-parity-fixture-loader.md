---
status: accepted
---
# One Python fixture loader for the parity corpus

Three suites (`test_serialize_fixtures`, `test_policy_fixtures`, `test_provider_fixtures`) each walk and parse `tests/parity/fixtures/` independently, and P5's tool replay would copy the walker a fourth time. `tests/support/fixtures.py` becomes the one loader: small frozen dataclasses (`Fixture`, `FixtureCall`), deterministic path sorting, JSON insertion order preserved, the originating fixture path and call id in every assertion failure, and pytest parameter ids derived from fixture and call ids. `question_from_wire` — today a private helper in the provider suite — moves there and gains the tool suites as consumers.

The Node harness stays Node. The two sides share the fixture data and schema; they do not share a parsing implementation. A cross-language fixture framework would be a seam with one consumer on each side and nothing varying across it.

The loader stays dumb: `fixtures.py` knows only the corpus format — paths, JSON, insertion order, tags. The replay engine is a separate `tests/support/replay.py` (fixture env, respx fake provider speaking the compatible envelope, the real server call, byte comparison) that builds on the loader; the loader never learns about providers or MCP. Pytest ids carry `fixture path :: call id`, so a byte-parity failure names its fixture.

## Considered Options

- **A fixture framework (tag filters, projections, per-suite plugins)** — rejected: the suites need a loader, not a framework; every capability added ahead of a second consumer is speculative.
- **Keep three walkers** — rejected: fixture-format knowledge needs one owner before P5 multiplies the consumers.

## Consequences

- The loader lands before the first tool replay test.
- A fixture-format change edits one Python module plus the Node harness.
- The P5 replay harness (fixture args + env + respx fake speaking the compatible envelope → byte-equal output text) builds on the loader and becomes the first Python test of the composed tool output — the wiring none of the fragment suites exercise.
