# Models

Repo-authored summary of TypeSafe's model page, in our own words. Last checked against the live
page on 2026-09-27; the live page wins where they differ. Every System One model is served at
`POST /v1/systemone`, and the request's `model` field picks one.

## Current (checked 2026-09-27)

| | |
| --- | --- |
| Versioned ID | `jev-1.13.0` |
| Aliases | `jev-latest` and `jev-preview` both resolve to `jev-1.13.0`; there is no separate preview build yet |
| Price | $42 / Btok input = $0.042 / Mtok. Output tokens free. |
| Rate limits | 250k tokens/s and 1,200 requests/min; TypeSafe says these move without notice for now. Over either → `429`. Enterprise plans go higher. |
| Context | 64k tokens/request (state + every question); 32k for `state` + the longest single question |
| Input | Text only: a string, a JSON object, or an array of text. Convert images, audio, and binaries to text first. |

Aliases move when a release ships, so answers behind them can shift. If you tuned thresholds on a
version, pin its versioned ID. The response's `model` field names the version that answered.

Jev uses the same weights for every account: no per-customer fine-tuning. Adapt it through
`state` (your own records and reference text), `instructions` and `criteria` (domain rules and
boundary cases), and by splitting broad judgments into atomic questions combined in code. English
is where accuracy is best; other languages, CJK included, work less well, so test first. TypeSafe
does not train on customer requests; enterprise zero data retention is available.

Errors worth handling: `429` (rate limit) and `529` (overloaded) both call for a retry with
exponential backoff; the SDKs do it for you.

On OpenRouter there is no `jev-latest` slug; this repo sends `typesafe/jev-1.13` there
(`src/jev_judge_mcp/providers/openrouter.py`).

Official: [models](https://docs.typesafe.ai/models.md) · [API errors](https://docs.typesafe.ai/api.md)

## Jaggedness (`jev-1.13`, reviewed 2026-09-17)

| Failure | Do this |
| --- | --- |
| Literal reading | Write the exact condition; put boundaries in criteria |
| Math, counting, numeric encodings (hex, RGB) | Compute in code; ask one noul per item and sum |
| Date/time order, duration, windows | Extract parts as choice (include "not stated"); compare in code |
| Indirection / double negatives | Direct instructions; name the relevant state paths |
| Large irrelevant state | Filter first; optional noul for relevance |
| Adversarial / injected instructions | Precise criteria; test edges |
| Contradictory instructions vs criteria | Align them |
| Expected identities (`P` + `P(not)` = 1, noul vs yes/no choice) | Ask each decision one way; enforce identities in code |
| Generation / free-form extraction | Candidate in code or a generative model; Jev selects |

Score interpolation between levels is weakly calibrated — threshold, don't reconstruct a magnitude.

Avoid: asking what code can compute; hiding several judgments in one question; System Two multi-hop reasoning; stuffing unused context.

Official: [jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md)

## Training (why not an LLM)

Jev is trained with **RLCD** (reinforcement learning for calibrated decisions): typed answers and probabilities, not preferred prose (RLHF) and not long chain-of-thought (RLVR). Calibration is a group property (`P=0.8` should be right ~80% of the time), not a guarantee on one answer.

TypeSafe's claim on System One tasks vs LLMs: orders of magnitude faster/cheaper; no string generation. Treat marketing multiples as theirs, not ours.

Official: [AI primer](https://docs.typesafe.ai/introduction/machine-learning-primer.md) · [manifesto](https://typesafe.ai/manifesto)
