# Models

All System One models share `POST /v1/systemone`. The `model` field selects which one.

## Current (fetched 2026-09-19)

| | |
| --- | --- |
| Versioned ID | `jev-1.13.0` |
| Aliases | `jev-latest` → `jev-1.13.0`; `jev-preview` → same (no preview build right now) |
| Price | $42 / Btok input = $0.042 / Mtok. Output tokens free. |
| Rate limits | 250k tokens/s and 1,200 rpm (dynamic; 429 over either). Higher on enterprise. |
| Context | 64k tokens/request; 32k for `state` + longest question |
| Input | Text only. String, object, or array of text. |

Pin `jev-1.13.0` if you have tuned thresholds against that version; aliases move on release. Response `model` reports the ID that answered.

Jev is not fine-tuned per account. Shape behavior with `state`, `instructions`, and `criteria`. Not trained on customer requests. English is the primary training language.

`evaluate` default: `jev-latest` (TypeSafe) or `~typesafe/jev-latest` (OpenRouter).

Official: [models](https://docs.typesafe.ai/models.md)

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
