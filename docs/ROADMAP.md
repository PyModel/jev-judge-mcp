# Roadmap — jev-judge-mcp

Behavior-compatible Python rewrite of the TypeScript reference server 0.5.0. Vocabulary: [CONTEXT.md](CONTEXT.md). Decisions: [docs/adr/](adr/). Frozen spec: [parity-manifest.json](reference/parity-manifest.json) + [ts-0.5.0-tools-list.json](reference/ts-0.5.0-tools-list.json). Every divergence from the reference — fixture-visible or not — is registered in [divergences.json](reference/divergences.json) (ADR-0020); the parity claim is stated per surface, never as one boolean. Transports are tiered by ADR-0021: stdio is the product (Tier A), `streamable-http` is experimental (Tier B).

## Headline release metrics

1. TS → Python deterministic parity: **100%** of fixtures, excluding tagged Sanctioned Divergences.
2. `jev_gate` false-AUTO rate: as close to 0 as calibration allows; accepted on the upper confidence bound, not the point estimate.
3. Agent cost/time saved at equal task quality, measured end to end.

## Layering (ADR-0002)

```
MCP ─▶ tool input validation ─▶ state + questions ─▶ Provider (envelope only)
    ─▶ Answer Validation (per tool) ─▶ Policy (pure) ─▶ JS-compatible JSON text (ADR-0006)
```

```
src/jev_judge_mcp/
  server.py settings.py limits.py errors.py text.py ids.py serialize.py telemetry.py
  stdio.py hook.py doctor.py cache.py keyfile.py setup.py fsutil.py redact_action.py
  domain/     questions.py answers.py usage.py json.py
  providers/  base.py resolver.py typesafe.py compatible.py openrouter.py cloudflare.py
  validation/ choice.py noul.py score.py extract.py numbers.py caps.py
  policy/     thresholds.py actions.py review.py claims.py ranking.py screen.py extract.py
  extract/    dialect.py worker.py candidates.py executor.py   # ADR-0004
  install/    engine.py layout.py launch.py verify.py …        # ADR-0033
  tools/      observed.py verify.py screen.py find.py classify.py rerank.py compare.py
              decide.py extract.py review.py gate.py score.py toolset.py arguments.py
tests/  unit/ contract/ parity/ property/ security/ integration/ evals/ load/
evals/  datasets/ manifests/ scorers/ runners/ baselines/ calibration/ reports/ ab/ bench/
```

Changes from the pasted plan: `validation/` split from `policy/` (validation rejects, policy decides); `serialize.py` (ADR-0006); `extract/` package (ADR-0004); no `vercel.py` (ADR-0007); `text.py` owns UTF-16 length/truncate (ADR-0005); `install/`, `hook.py`, `doctor.py`, `cache.py`/`keyfile.py`/`setup.py` (ADR-0033/0046/0047), and `tools/score.py` (ADR-0048) arrived with their phases.

## Dependencies

| Purpose | Package | Note |
|---|---|---|
| MCP | `mcp>=2.2,<3` | `MCPServer`, verified in 2.2.0 wheel |
| HTTP | `httpx` | all raw providers |
| Models/config | `pydantic>=2`, `pydantic-settings` | |
| TypeSafe | `typesafe-sdk` (optional extra) | **not** `typesafe` — unrelated package |
| Regex | — (stdlib `re`) | `regex` finishes catastrophic patterns V8 cannot; ADR-0018 |
| TOML | `tomlkit` | the installer edits `~/.codex/config.toml` in place |
| Dev | `pytest`, `anyio`, `respx`, `hypothesis`, `pytest-cov`, `ruff`, `pyright`, `pytest-benchmark` | |
| Telemetry (extra) | `opentelemetry-sdk`, `prometheus-client` (HTTP mode only) | |

---

## Phases

Each phase ends only when its acceptance checks run green in CI. Epic IDs in brackets.

### P0 — Spec freeze [PY-01] ✅ done

- [x] Reference pinned: commit `69ffb4b`, tree `aa89f96`, v0.5.0.
- [x] `tools/list` snapshot captured from the built TS server.
- [x] Caps, defaults, policy, IDs, regex pipeline, provider resolution, quirks → `parity-manifest.json`.
- [x] Decide keep/diverge for quirks Q2–Q4 (ADR-0012). Q5 is withdrawn. Q1, Q6–Q10 are in ADR-0003/5/6/8/11.
- [x] Accept ADR-0004 (unit-space subset, process pool).
- [x] Fill the manifest's `not_captured` gaps as parity fixtures: question/criteria strings, output key order for find/classify/rerank/compare, handler error strings.
- [x] Build the **fixture harness**: a fake provider server that replays `{answers, usage, model}` per request; run the TS server against it and record `(tool args, fixture) → output text` for every mock-suite case (60) plus the fixture classes below. Store under `tests/parity/fixtures/`.

Every `known_reference_quirks` entry cites `file:line` at commit `69ffb4b`.

Fixture classes: normal · boundary · malformed · truncated · low-confidence · tie · contradicted · unsupported · zero-match · regex-timeout · invalid-pattern · provider-failure · resolver-error · unicode/astral · duplicate-id.

The harness pins Node 24 (the audit ran on v24.19.0). The fake speaks the `compatible` envelope: `usage` may be absent, and a string `body.model` overrides the requested model in the recorded output (`provider.ts:195-199`). Record that model. `resolver-error` launches the pinned TS server under an env matrix (missing key, `OPENROUTER_API_KEY` failing `^sk-or-`, each explicit `JEV_PROVIDER` with its credential unset). Those throw before any HTTP. Record the error text. No network and no fake server on that class.

**Accept:** manifest + fixtures committed; `make parity-record` regenerates them byte-identically from the pinned TS commit.

### P1 — Foundation [PY-02]

`pyproject.toml` (dist `jev-judge-mcp`, script `jev-judge-mcp`, ADR-0049), `uv.lock`, src layout, `MCPServer` over stdio, optional Streamable HTTP, settings from env only (ADR-0008), stderr logging, CI skeleton.

**Accept:**
- `uvx --from . jev-judge-mcp` starts; initialize succeeds; `serverInfo.name == "jev-mcp"`.
- Negotiated protocol with a `2025-06-18` client proven (ADR-0010 open point).
- `tools/list` (once tools exist) equals the TS snapshot under the ADR-0010 diff: parse both, sort object keys recursively, exact equality of `name`, `title`, `description`, `inputSchema`, and `execution`. `serverInfo.name` is `jev-mcp`; `serverInfo.version` is this distribution's version and is not compared.
- stdout carries only protocol frames (test spawns the server and parses every line).
- SIGINT/SIGTERM exit cleanly with no traceback.

### P2 — Domain, validation, text [PY-03]

Canonical `ChoiceQuestion/NoulQuestion/ScoreQuestion` and answers owned by us, not SDK classes. Validators: `validate_choice(answer, expected_keys)`, `validate_noul`, `validate_score`, `margin`. UTF-16 `length/truncate` (ADR-0005), `sanitize_id/ensure_unique_ids`, JS serializer + `to_fixed` (ADR-0006).

**Accept:** every validation edge case from the TS unit + mock suites has a Python test; Hypothesis properties: non-argmax never validates; NaN/Inf/negatives/sum∉[1±tol] never validate; serializer round-trips match Node `JSON.stringify` on 10k generated payloads including `-0.0`, subnormals, the `1e21` and `1e-7` boundaries, and sums of the `0.1+0.2` class (differential test calls Node 24 in CI).

### P3 — Policy engine [PY-03]

Pure ports of `verifyAction`, `screenRecommendation`, `existsVerdict`, `rankCandidates`, `rerankByScore`, `classificationDecision`, `resolvePolicyThresholds`, `reviewComposite`, `reviewAction`, `claimAction`, `worstAction`, `requireCompleteContext`, `contradictsRecommendation`, gate reason-code assembly.

**Accept:** 100% branch coverage; property tests: truncated ⇒ never auto; unknown confidence ⇒ never auto (even at threshold 0); worst_action is monotone.

### P4 — Providers [PY-04, PY-09]

`JevProvider.evaluate(state, questions, model, timeout) -> Evaluation`. Resolver reproduces the auto order including the Vercel slot (ADR-0007). Uniform envelope validation (ADR-0003), redaction on every provider (ADR-0008), cancellation propagates to `httpx` (ADR-0011 — the reference does not cancel, so this is a divergence, tested Python-only).

Order: compatible (P0 prio, easiest to fake) → TypeSafe via `typesafe-sdk` → OpenRouter (`jev-latest → jev-1.13`, then a `typesafe/` prefix unless the slug already has one) → Cloudflare (double-nested `result.result`; `state` errors only when it is a string other than `Completed`). Redaction covers the ADR-0008 secret list.

**Accept:** one shared contract suite per provider: success, malformed body, non-object answers, bad usage, non-string model, HTTP error with key echoed in body (must be redacted), timeout, client cancel, model-name passthrough. Adapters may differ only in URL, auth, slug, request/response envelope, usage.

### P5 — Tools, in dependency order [PY-05, PY-06, PY-07, PY-08]

| # | Tool | Introduces | Watch |
|---|---|---|---|
| 1 | `jev_verify` | Choice + evidence, `source_*` aux question | Q4 confidence strictness |
| 2 | `jev_screen` | Noul, block/review/skip | 0.3 skip thresholds hardcoded |
| 3 | `jev_find` | Choice + Noul together, `exists_verdict` | missing exists ≠ absent |
| 4 | `jev_classify` | opaque keys, batched Choice | items×classes ≤ 8,000 |
| 5 | `jev_rerank` | per-candidate relevance | 100k aggregate chars |
| 6 | `jev_compare` | repeated Choice, aspects | confidence already range-checked (ADR-0012); Q5 withdrawn |
| 7 | `jev_decide` | escape hatches, requirement checks | Q3 silent invalid checks |
| 8 | `jev_extract` | regex dialect + killable worker (ADR-0004) | Q2 tolerance; zero-call path |
| 9 | `jev_review` | Score + Noul + composite | anti-injection framing text verbatim |
| 10 | `jev_gate` | full composition + reason codes | reason-code order frozen |

`jev_score` (ADR-0048) publishes after the ten as the first extension tool: the snapshot order stays a frozen prefix, and the extension carries its own pinning tests, caps owner, and L3 scorer.

Question instruction strings and criteria text are part of the spec — copy verbatim from the reference; they change model behavior.

**Accept per tool:** all its parity fixtures byte-equal (or tagged divergence); its TS mock tests ported; fail-closed matrix (missing, wrong type, extra key, missing key, non-argmax, sum 0.99/1.01, NaN, Inf, negative, malformed confidence) produces no `auto`.

**`jev_extract` hard invariants:** `value ∈ candidates(document) ∪ {null}` — any violation is a release blocker, not a metric; zero candidates ⇒ no provider call; truncated/too-long universe ⇒ never `auto` or definite `not_found`; a timing-out pattern returns `invalid_pattern` within ~1.2 s and the server keeps serving concurrent calls.

### P6 — Security & fuzz [PY-10] ✅ done

The offline scripted adversary is `tests/security/` on `make ci`; `make security-live` is the bounded paid TypeSafe gate.

Separate CI stage. Prompt-injection strings in every text field (request, diff, tests, claims, evidence, candidates, class descriptions, passages); credential reflection in HTTP error bodies for every secret in the ADR-0008 list, including base URLs that carry userinfo; ReDoS corpus; astral/combining/RTL Unicode; duplicate and 64+-char IDs; oversized everything; NaN/Inf answers; broken connections; cancellation mid-request; 64 concurrent calls; stdout contamination. Injection tests here check *policy* holds under adversarial answers (mocked); model robustness is L3. The astral regex corpus is the ADR-0004 fuzz: in-subset patterns, U+1F600 beside a BMP character, unpaired-surrogate matches, `\s` against U+00A0 and U+0085.

### P7 — Eval framework [PY-11, PY-12, PY-13] ◐ in progress

The offline framework and the L4 agent outcome study exist (`evals/`, `make eval` / `eval-live` / `ab`); the
L3-sized datasets and the live calibration campaign are still open (see `evals/README.md` gaps).

Four layers, never mixed:

| Layer | Question | Provider | Where |
|---|---|---|---|
| L1 software correctness | does the code do what it says | mocked | every CI run |
| L2 TS↔Py parity | same output for same answers | fixture replay | every CI run |
| L3 judgment quality | are Jev's judgments right and calibrated | live, **pinned model** | nightly / manual |
| L4 agent utility | do agents do better with it | live | per release |

Per-tool L3 datasets and primary metrics:

| Tool | Primary metric | Also |
|---|---|---|
| verify | contradiction recall | macro-F1, Brier, ECE, selective accuracy |
| screen | injection recall at fixed false-block rate | PR-AUC, skip precision |
| find | Recall@1, exists AUROC | MRR, NDCG@10, verdict accuracy |
| rerank | NDCG@10 vs BM25/embeddings/original order | MRR, Kendall τ |
| classify | selective accuracy of AUTO | macro/micro-F1, auto coverage |
| decide | overdecision rate | escape-hatch accuracy, requirement-check F1 |
| compare | contradiction recall on numeric/negation perturbations | aspect-level F1 |
| extract | exact match; hallucinated values = 0 | not-found P/R |
| review | P(defective \| AUTO) | safe_to_apply AUROC, severe-defect recall |
| gate | false-AUTO rate | claim macro-F1, reason-code accuracy |
| score (ADR-0048) | nearest-level accuracy | level MAE, within-one rate, gold-level probability |

Calibration: split 60/20/20 dev/calibration/locked-test **by source family** (repo, document family, template), never by row. Pick thresholds as max AUTO coverage subject to the Wilson/Clopper-Pearson upper error bound ≤ target. Starting precision targets (design goals, not claims): classify/find ≥97%, compare ≥98%, extract/verify/review ≥99%, gate ≥99.5%. Repeat borderline cases (±0.05 of threshold) 3–5× to measure flip rate; widen margins where flips are frequent.

**Note:** any L3-driven change to a frozen default is a Sanctioned Divergence and needs an ADR. Screen's substance/relevance skip thresholds, hardcoded at 0.3 in the reference, are the current case.

### P8 — Agent A/B [PY-14] ✅ done

The 2026-09-22 outcome study (18 runs, 9 measured pairs, Claude Code `claude-sonnet-5`) is recorded in `evals/reports/agent-outcomes.md` and quoted in README § Agent outcomes; the three-arm DeepSeek bench (`evals/reports/bench150.md`) answers the same question over judgment items.

Does Jev improve a coding agent (Claude Code, Pi) while it uses the Jev MCP tools? A: no Jev · B: the Python server. Same agent, model, temperature, repo snapshot, tools, limits, timeout, and grader; B adds only the server and one sentence naming the task's Jev tool. Tasks hinge on a judgment Jev is for. A pair counts only when both arms reached the model and B got a Jev answer through the MCP. Report from measured pairs: tasks solved, time to a correct solution (median and spread), correct solutions per hour, wrong branches, retries, tool calls and tokens per solved task, judge accuracy where gold exists. No measured pair means "not measured". Design and history: ADR-0030. The provider latency claim (70–500 ms) and the Jev price ($0.042/M input, applied to reported tokens) are recorded per Jev call in the raw records, not assumed.

### P9 — Observability & load [PY-15] ◐ in progress

In-process spans, metrics, and the 64-concurrent load gate are landed (`src/jev_judge_mcp/telemetry.py`,
`make load`); HTTP-mode export (OpenTelemetry, a Prometheus endpoint) is not built.

Spans `mcp.tool`, `jev.evaluate`, `jev.validate`, `regex.extract` with counts/flags/durations only — never evidence, claims, diffs, candidate text, or keys unless an explicit debug env flag is set. Metrics: calls, provider errors, fail-closed, auto/review/escalate, truncated, regex timeouts, tokens, durations. Load at 1/4/16/32/64 concurrent calls; local overhead excluding provider: p50 < 5 ms, p95 < 20 ms.

### P10 — Rollout [PY-16]

A mock parity 100% → B shadow (TS authoritative, Python non-authoritative; measure action agreement, latency, errors) → C canary on internal agents → D Python default, TS kept as fallback. Rollback = swap the MCP command back to the TypeScript reference server and restart; no state to migrate. Rehearse rollback once before D.

---

## CI

`ruff → pyright → unit → property → contract → parity (Node 24 for fixture replay + differential tests) → security → build → MCP smoke`. Live evals are separate (cost, provider availability, model drift) and pin the exact Jev model; production may still default to `jev-latest`.

## 1.0 release gates

- [ ] the snapshot ten plus ADR-0048 extensions; `tools/list` carries the snapshot as a frozen prefix under the ADR-0010 diff
- [ ] 100% parity fixtures pass; every divergence tagged to an accepted ADR
- [ ] 0 fail-open results in the malformed-answer matrix
- [ ] 0 extracted values outside regex candidates
- [ ] pathological regex cannot stall the server (concurrent-call test)
- [ ] secrets redacted in errors/logs for every provider
- [ ] cancellation verified per provider (ADR-0011; no TS counterpart)
- [ ] stdio stream clean
- [ ] live E2E green: TypeSafe, OpenRouter, Cloudflare, compatible
- [ ] AUTO thresholds calibrated on held-out data; gate false-AUTO meets target on its upper bound
- [ ] 64-concurrent load test passes
- [ ] rollback rehearsed and documented

## Decisions

| ID | Outcome | Where |
|---|---|---|
| D1 | Keep extract's `<= 0.01` check. A 0.99 sum is observable: extract rejects it, the shared tolerance accepts it. | ADR-0012 |
| D2 | Keep decide's invalid checks visible in `checks` and absent from contradiction warnings. | ADR-0012 |
| D3 | Keep Q4. Q5 is withdrawn; compare already range-checks confidence. | ADR-0012 |
| D4 | Subset, UTF-16 unit space, process pool. Embedded JS stays a revisit if the subset is too small. | ADR-0004 |
| D5 | Keep sequential fields inside one extract call. Duration is not output. | ADR-0012 |
