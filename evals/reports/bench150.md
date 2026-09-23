# Jev bench: direct, automatic, and forced

Accuracy: not measured (0 labeled items).

Model: opencode-go/deepseek-v4.1-flash.

Thinking: high.

Jev spend is capped at 25 USD. Agent model spend is separate and is not measured when Pi reports no usage cost.

| arm | triplets | median wall s | p90 | p95 | Jev spend USD | called Jev |
|---|---|---|---|---|---|---|
| A direct | 150 | 3.06 | 5.28 | 6.17 | 0.0000 | 0 |
| B automatic | 150 | 2.91 | 4.86 | 8.09 | 0.0000 | 0 |
| C forced | 150 | 13.95 | 21.01 | 28.67 | 0.0060 | 150 |

| paired difference | median s | p90 | p95 |
|---|---|---|---|
| B minus A | -0.10 | 1.03 | 2.04 |
| C minus A | 10.43 | 15.99 | 21.56 |

Automatic arm: 0 of 150 B runs called Jev; 150 did not call Jev.

Forced arm: 149 of 150 model-reached C runs got a Jev answer.

Jev connection (model-reached B and C runs with a Jev answer): 149/300 (50%).

Jev round trip: n=157, median 464.6 ms, p90 1245.3 ms, p95 1468.8 ms.

Tokens (Pi usage fields, summed): A input 447114 / output 42804 / cache read 30592 / cache write 0; B input 438467 / output 49315 / cache read 39680 / cache write 0; C input 1230791 / output 167029 / cache read 1860608 / cache write 0.

Agent model spend (Pi usage cost, summed): A $0.0928; B $0.0955; C $0.2904.

Stop: all 150 triplets recorded

History: the 2026-09-22 two-arm Pi run recorded 150 pairs; after 43 with-Jev runs reached the local model, 127.0.0.1:8000 stopped accepting connections (Pi reported Connection error with zero tokens, on both arms) and the remaining 107 runs were server failures, not model speed.

Speed and time use triplets where every arm reached the model. Source: run records `evals/reports/bench150/*/result.json` (raw transcripts gitignored).
