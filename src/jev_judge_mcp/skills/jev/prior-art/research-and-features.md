# Shape: Jev Answers as Data

**Use when** the output is not an action but a measurement: features for a classical model, labels for a dataset, variables for a study, or a benchmark of Jev itself.

## The shape

```
rows → one request per row with N questions → a numeric table
     Noul  → 1 column (P(yes))
     Score → 2 columns (mean level, spread) or one column per level probability
     Choice→ one column per option probability
→ train (CatBoost/logistic), correlate with outcomes, or compare with human data
```

```python
def to_features(r) -> dict[str, float]:
    row = {}
    for qid, a in r.nouls.items():
        row[qid] = a.noul
    for qid, a in r.scores.items():
        mean = a.score
        row[f"{qid}_mean"] = mean
        row[f"{qid}_sd"] = sum(p * (lvl - mean) ** 2 for lvl, p in a.probabilities.items()) ** 0.5
    for qid, a in r.choices.items():
        row.update({f"{qid}={opt}": p for opt, p in a.probabilities.items()})
    return row
```

## Field lessons

- Many narrow features + a trained model beat asking Jev for the target directly (RMSE 1.77 vs. 2.15).
- An LLM can propose the candidate questions. Keep the ones that improve held-out error (an autoresearch loop).
- Research designs that use the calibrated probabilities themselves are a novel angle. Example: do option probabilities match how human test-takers distribute their answers?
- For studies, pin the model ID and report it. Measure run-to-run stability (std ≈ 0.01) before you trust small effects.
- Calibration varies by domain. Check it against labels before you treat a probability as a frequency.

