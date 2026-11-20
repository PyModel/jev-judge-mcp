# Architecture decision records

Numbers 0037–0041 and 0044 were never written. Amendments appear in the owning record.

| Number | Decision | Status |
| --- | --- | --- |
| 0001 | [Behavior-compatible rewrite against a frozen TypeScript reference](0001-behavior-compatible-rewrite-against-frozen-ts-reference.md) | Accepted |
| 0002 | [The model judges; deterministic Python policy decides](0002-model-judges-policy-decides.md) | Accepted |
| 0003 | [Validate the transport envelope identically for every provider](0003-uniform-envelope-validation-across-providers.md) | Accepted |
| 0004 | [jev_extract accepts a restricted ECMAScript subset, matched in UTF-16 units, in a killable process pool](0004-extract-regex-dialect-and-isolation.md) | Partially superseded |
| 0005 | [Caps and truncation count UTF-16 code units, not Python characters](0005-lengths-measured-in-utf16-code-units.md) | Accepted |
| 0006 | [Tool results are serialized to match JSON.stringify(payload, null, 2)](0006-js-compatible-result-serialization.md) | Accepted |
| 0007 | [Vercel AI Gateway keeps its resolution slot but is unsupported in Python 1.0](0007-vercel-provider-reserved-not-emulated.md) | Accepted |
| 0008 | [Provider URLs and credentials come only from process configuration](0008-provider-endpoints-and-credentials-are-process-config-only.md) | Partially superseded |
| 0009 | [Publish as `jev-mcp-python`, never document `uvx jev-mcp`](0009-distribution-name-jev-mcp-python.md) | Superseded by ADR-0049 |
| 0010 | [Python 3.12+ on the MCP Python SDK v2 (`MCPServer`)](0010-python-312-and-mcp-sdk-v2.md) | Partially superseded |
| 0011 | [Propagate MCP client cancellation to the provider request](0011-propagate-mcp-cancellation-to-providers.md) | Accepted |
| 0012 | [Keep the reference's per-tool validation quirks; Q5 is withdrawn](0012-keep-reference-validation-quirks.md) | Accepted |
| 0013 | [One registry, one execution kernel, ten declarative tool definitions](0013-one-registry-one-execution-kernel-ten-tool-definitions.md) | Accepted |
| 0014 | [Cap values, measurement, and enforcement are three modules](0014-cap-values-measurement-and-enforcement-are-separate.md) | Accepted |
| 0015 | [One Python fixture loader for the parity corpus](0015-one-python-parity-fixture-loader.md) | Accepted |
| 0016 | [extract: the dialect translates; executors execute and own time](0016-extract-dialect-translates-executors-execute.md) | Accepted |
| 0017 | [The redaction secret set derives from the Settings schema](0017-redaction-secret-set-derives-from-settings-schema.md) | Accepted |
| 0018 | [jev_extract matches with stdlib `re`, and the subset is narrower than ADR-0004's list](0018-extract-runs-on-stdlib-re-with-a-narrower-subset.md) | Accepted |
| 0019 | [Parsed-JSON value-shape primitives live in `domain/json.py`](0019-parsed-json-value-shape-primitives-live-in-domain.md) | Accepted |
| 0020 | [The divergence registry is the single source of divergence truth](0020-divergence-registry-single-source-of-truth.md) | Accepted |
| 0021 | [stdio is the product transport; streamable-http is Tier B experimental for 1.0](0021-stdio-is-the-product-transport-http-is-tier-b.md) | Partially superseded |
| 0022 | [Published schemas are proven faithful to the argument validator at construction](0022-schemas-are-validated-faithful-at-construction.md) | Accepted |
| 0023 | [Same-origin redirects only on the HTTP providers](0023-same-origin-redirects-only.md) | Partially superseded |
| 0024 | [Record the non-fixture divergences (N2, N5, T7, T9)](0024-record-non-fixture-divergences.md) | Accepted |
| 0025 | [Extract pool saturation has its own reason, distinct from the regex timeout](0025-extract-pool-saturation-has-its-own-reason.md) | Accepted |
| 0026 | [Paid eval harnesses share one agent run, one relay, and one spend ledger](0026-paid-eval-harness-shares-one-agent-run-one-relay-one-ledger.md) | Accepted |
| 0027 | [One contract test guards every paid path; runtime guards stay per runner](0027-one-contract-test-guards-every-paid-path.md) | Accepted |
| 0028 | [Scorers expose per-tool judgments and are pinned to real server output](0028-scorers-expose-judgments-pinned-to-real-output.md) | Accepted |
| 0029 | [The OpenRouter HTTP-Referer names this repository](0029-openrouter-referer-names-this-repo.md) | Accepted |
| 0030 | [L4 measures agent outcomes, and only from runs where the agent used Jev](0030-l4-measures-agent-outcomes-only-when-the-agent-uses-jev.md) | Accepted |
| 0031 | [Classify rejects a generated id that collides with a supplied id](0031-classify-rejects-generated-id-collisions.md) | Superseded by ADR-0085 |
| 0032 | [The server is POSIX-only until Windows is implemented and tested](0032-posix-only-until-windows-is-tested.md) | Accepted |
| 0033 | [One command installs the `jev` MCP entry into local agent configs](0033-one-command-agent-installer.md) | Partially superseded |
| 0034 | [Action-pattern redaction sits beside the configured-secret redactor](0034-action-pattern-redaction-beside-configured-secrets.md) | Partially superseded |
| 0035 | [The command hook is not jev_gate](0035-command-hook-is-not-jev-gate.md) | Partially superseded |
| 0036 | [The bench records an automatic arm that does not call Jev](0036-bench-records-automatic-jev-non-use.md) | Partially superseded |
| 0042 | [Invalid answers fail closed through one policy token](0042-fail-closed-policy-token.md) | Accepted |
| 0043 | [Choice confidence is absent, malformed, or a number](0043-three-valued-choice-confidence.md) | Accepted |
| 0045 | [One study runner owns the paid run protocol](0045-study-runner-owns-the-paid-run-protocol.md) | Accepted |
| 0046 | [A stored API key and a `setup` command onboard TypeSafe without an export](0046-stored-api-key-and-setup-command.md) | Partially superseded |
| 0047 | [An optional response cache replays identical requests verbatim](0047-optional-response-cache-replays-verbatim.md) | Accepted |
| 0048 | [Extension tools are published after the frozen ten, registered as divergences](0048-extension-tools-after-the-frozen-ten.md) | Accepted |
| 0049 | [Publish as `jev-judge-mcp`](0049-distribution-name-jev-judge-mcp.md) | Accepted |
| 0050 | [The HTTP transport refuses to run without access control](0050-http-access-control-refuses-unauthenticated-start.md) | Partially superseded |
| 0051 | [After publication, install launches the version-pinned PyPI package by default](0051-launch-identity-after-publication.md) | Partially superseded |
| 0052 | [Compare warns when an aspect contradicts the overall relation](0052-compare-warns-when-aspects-contradict-the-overall.md) | Accepted |
| 0053 | [Installer entries request a qualifying Python, and an unreleased tree says so](0053-installer-entries-request-a-qualifying-python.md) | Accepted |
| 0054 | [Report a checkout's build on the wire](0054-build-identity-on-the-wire.md) | Accepted |
| 0055 | [Default the HTTP port to 8088, and exit cleanly when it stays taken](0055-http-port-in-use-exits-clean.md) | Partially superseded |
| 0056 | [The pre-push gate: every ci.yml command, natively and on Linux, before any push](0056-pre-push-ci-gate.md) | Accepted |
| 0057 | [One bounded retry policy owns upstream Jev retries](0057-one-retry-owner-bounded-provider-retries.md) | Partially superseded |
| 0058 | [The server starts its regex pool warm](0058-the-server-starts-its-regex-pool-warm.md) | Accepted |
| 0059 | [The fake-secret guard scans every repo-owned surface tests can carry a secret through](0059-fake-secret-guard-scans-every-test-carried-surface.md) | Accepted |
| 0060 | [Release-PR CI is maintainer-approved](0060-release-pr-ci-is-maintainer-approved.md) | Accepted |
| 0061 | [Initialize instructions are a registered divergence](0061-initialize-instructions.md) | Partially superseded |
| 0062 | [Additive response fields stay beside the old keys](0062-additive-response-fields.md) | Partially superseded |
| 0063 | [Gate claim checks may use diff and tests](0063-implicit-diff-and-tests-evidence.md) | Accepted |
| 0064 | [The CLI is the harness-agnostic path](0064-cli-judge-and-gate.md) | Partially superseded |
| 0065 | [Amendment to ADR-0035: opt-in required hook](0065-required-hook-flag.md) | Partially superseded |
| 0066 | [A file list is reviewed per file, not silently cut](0066-multi-file-diff.md) | Partially superseded |
| 0067 | [Evidence may name its kind, role, and whether tests were hashed](0067-evidence-metadata.md) | Partially superseded |
| 0068 | [Verify carries the anti-injection sentence; request id is not invented](0068-verify-anti-injection-and-request-id.md) | Accepted |
| 0069 | [An optional cap on concurrent provider requests](0069-optional-inflight-cap-on-provider-requests.md) | Accepted |
| 0070 | [Calibrate is advisory only](0070-calibrate-is-advisory-only.md) | Accepted |
| 0071 | [The connecting agent can read the jev skill](0071-packaged-jev-skill.md) | Accepted |
| 0072 | [An upstream 401 is auth](0072-upstream-401-is-auth.md) | Accepted |
| 0073 | [Added tests do not fail a correct L4 run](0073-added-tests-do-not-fail-a-correct-run.md) | Accepted |
| 0074 | [Live agent evals run confined: a broker sidecar, a capability, and a container with no credential](0074-confined-live-agent-runs-broker-sidecar-unix-socket.md) | Accepted |
| 0075 | [A selected provider that cannot answer any call refuses at startup](0075-selected-uninstallable-provider-refuses-at-startup.md) | Partially superseded |
| 0076 | [The hook gate asks guard questions, not one bare allow](0076-hook-guard-questions.md) | Partially superseded |
| 0077 | [Caller-written questions and server-read files: the ask and file-judgment extensions](0077-ask-and-file-judgment-extensions.md) | Partially superseded |
| 0078 | [Shared owners for the review half, the file-list pass, and the hook runtime](0078-shared-owners-for-the-review-half-the-file-list-pass-and-the-hook-runtime.md) | Partially superseded |
| 0079 | [An upstream context overflow is input_too_large](0079-upstream-context-overflow-is-input-too-large.md) | Partially superseded |
| 0080 | [jev_gate_range reads the diff server-side](0080-gate-range-reads-the-diff-server-side.md) | Accepted |
| 0081 | [Explain held gates and bound the context shared between file reviews](0081-gate-diagnostics-and-file-context.md) | Partially superseded |
| 0082 | [Actionable completion gate escalations](0082-actionable-gate-escalation.md) | Partially superseded |
| 0083 | [Fail-closed tool results and provenance](0083-fail-closed-tool-results.md) | Accepted |
| 0084 | [TypeSafe shares the HTTP provider](0084-typesafe-shares-the-http-provider.md) | Accepted |
| 0085 | [Use one caller ID policy](0085-shared-caller-id-policy.md) | Accepted |
| 0086 | [Shared provider request budget and gate claim packing](0086-shared-provider-window-and-gate-packing.md) | Partially superseded |
| 0087 | [Per-call security guard and local file boundaries](0087-per-call-security-guard-and-local-file-boundaries.md) | Accepted |
| 0088 | [Commands start without forking the server](0088-commands-start-without-forking-the-server.md) | Accepted |
| 0089 | [Held rows say who settles them](0089-gate-says-who-settles-a-held-row.md) | Accepted |
