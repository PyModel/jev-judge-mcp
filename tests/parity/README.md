# Parity fixtures

Recorded behavior of the TypeScript reference server 0.5.0 at commit `69ffb4b` (tree `aa89f96`), run on Node v24.19.0. ROADMAP P0 and ADR-0001 define the target. A Python parity test replays each fixture and must produce the same `result`, byte for byte, unless the fixture lists a divergence.

```
make parity-reference   # clone + build the reference into tests/parity/reference/ (gitignored; network once)
make parity-record      # regenerate fixtures/ from the reference; offline
make parity-verify      # replay every fixture against the reference; results must match byte for byte
```

`NODE` defaults to `~/.nvm/versions/node/v24.19.0/bin/node`. The harness refuses any other Node version.

## Layout

| Path | What |
|---|---|
| `fixtures/mock/NNN-*.json` | One per runtime case of the reference `test/mock.test.mjs` (152), in suite order |
| `fixtures/classes/<class>/*.json` | Cases for the ROADMAP P0 fixture classes, defined in `cases/*.mjs` |
| `fixtures/index.json` | Every fixture with its classes, divergences, tools, and call/request counts |
| `cases/` | Class case definitions: tool arguments, env overrides, and the provider responses to replay |
| `harness/` | Fake provider, recorder, replayer, reference fetch script |

Python suites share one loader, `tests/support/fixtures.py` (ADR-0015), and replay through `tests/support/replay.py`; the Node harness stays Node. The two sides share the fixture data and schema, not a parsing implementation.

## Fixture format

```jsonc
{
  "id": "normal/gate-accepted",
  "source": { "case": "..." },            // or { "file": "test/mock.test.mjs", "test": "<name>" }
  "classes": ["normal"],                    // "mock" for the reference suite
  "divergences": [],                        // e.g. ["ADR-0004"]: Python is expected to differ
  "note": "...",                            // optional
  "calls": [{
    "env": { "JEV_PROVIDER": "compatible", "JEV_API_KEY": "...", "JEV_API_BASE_URL": "{{FAKE_URL}}/v1/systemone" },
    "tool": "jev_gate",
    "arguments": { ... },
    "exchanges": [{
      "request":  { "method": "POST", "path": "/v1/systemone", "authorization": "Bearer ...",
                    "content_type": "application/json", "body": { "model": ..., "state": ..., "questions": ... } },
      "response": { "status": 200, "body": "<exact response text>" }
    }],
    "result": { "content": [{ "type": "text", "text": "<tool output>" }], "isError": true }  // isError only when set
  }]
}
```

The server environment is exactly `env` plus the MCP SDK defaults (HOME, LOGNAME, PATH, SHELL, TERM, USER). Replace `{{FAKE_URL}}` with the fake provider's origin. Serve `exchanges[i].response` for the i-th request. `request` is what the reference sent. It is where the question instruction strings, criteria maps, and the ANTI_INJECTION and gate framing text are fixed. Match it on parsed JSON, including key order. `result` is the MCP `CallToolResult`. `content[0].text` is the tool output that parity compares. A JSON-RPC failure would appear as `result.protocol_error`. None of the current fixtures has one.

## Recording rules

- Every provider call goes to the harness fake over the `compatible` envelope (`provider.ts:158-201`). A string `body.model` overrides the requested model in the output, and fixtures record it.
- `resolver-error` cases start the server without a fake and without network. Resolution throws inside `askJev` before any HTTP (`provider.ts:35-77`). Fixtures record the tool error text.
- The mock suite runs unmodified except for three swaps (`harness/mock-hooks.mjs`). `withMock` points the server at the fake as `compatible` instead of TypeSafe. `assert` records failures and does not throw. `test` writes one fixture per case. `reference_assertions_failed_under_compatible` lists each TypeSafe-path assertion that does not hold under `compatible`. There are nine such cases: three exact-payload checks that expect `provider: "typesafe"`, and six non-object `answers` envelopes. Under `compatible` those six are transport errors, not per-tool `invalid_response` (ADR-0003 applies the same rule to every Python provider).
- Fixtures carry no ports, timestamps, durations, or absolute paths. `make parity-record` twice in a row produces identical bytes.

## Divergence tags

| Tag | Fixtures |
|---|---|
| `ADR-0004` | `invalid-pattern/*`. The reason text is V8's, or V8 accepts a pattern outside the Python subset (`u`, `m`, variable lookbehind, named groups). |
| `ADR-0005` | `unicode-astral/*-splits-surrogate*`. `truncate()` leaves a lone high surrogate. |
| `ADR-0007` | `resolver-error/explicit-vercel-unset` |
| `ADR-0031` | `duplicate-id/classify-omitted-ids-get-positional-fallbacks`. The reference returns two rows with the same id. Python raises `Duplicate item id` and makes no provider request. |

## Notes from recording

- The manifest counts 60 mock tests. The pinned `test/mock.test.mjs` has 66 `test(` call sites: 59 top level and 7 inside loops. At runtime they expand to 152 cases, and all 152 are recorded.
- The reference's TypeSafe, OpenRouter, and Cloudflare providers turn a missing or non-object `answers` into `{}` (`provider.ts:149`, `provider.ts:256`), and each tool then reports per-item `invalid_response`. `compatible` raises a transport error for the same input. The fake speaks only `compatible`, so the other providers' branch is not recorded. ADR-0003 applies the `compatible` rule to every Python provider, so no fixture here carries `divergence:ADR-0003`. P4's provider contract suite owns that case.
- Handler errors (`throw new Error` in a tool handler) come back as `isError` with the bare message. Schema rejections come back as `isError` with the MCP SDK's text, `MCP error -32602: Input validation error: ...`. That text belongs to the TypeScript SDK and zod, not to the reference code.
- The reference `jev_classify` does not check generated fallback ids against supplied ones. An item without an id next to an item with id `item0` produces two results with id `item0`, and colliding class ids merge in `probabilities` and `by_class` (`duplicate-id/classify-omitted-ids-get-positional-fallbacks`). `jev_rerank` does check them (`index.ts:711-722`). Python rejects that collision with `Duplicate {kind} id` before any provider request (ADR-0031). The fixture is tagged `ADR-0031`.
- ADR-0004 quotes the V8 invalid-group message as `/(?//:`. The reference always appends `g`, so the recorded text is `Invalid regular expression: /(?//g: Invalid group`.
- Two budget-edge successes are not recorded: `jev_classify` at exactly 8,000 item-class pairs and `jev_rerank` at exactly 100,000 characters. Each would need a fixture of several hundred KB. The over-budget errors are recorded.
