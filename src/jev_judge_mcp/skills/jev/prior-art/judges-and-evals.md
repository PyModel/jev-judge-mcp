# Shape: Judges and Evals

**Use when** you need to grade model outputs, agent traces, content, notes, or submissions against a rubric, online or offline.

## The shape

```
artifact (+ reference, policy) → atomic rubric questions (one flaw or dimension each)
                               → code: max over failure flags, weighted sum over quality dimensions
                               → gold-label test set that measures agreement; diff what flips when questions change
```

```python
from typesafe_sdk import Choice, Noul, Score

TRACE_RUBRIC = {
    "needs_review": Noul(instructions="Should a human review the agent run in `trace`?"),
    "user_disagrees": Noul(instructions="Does the user reject or correct the assistant in `trace.messages`?"),
    "failure_mode": Choice(
        instructions="What is the main way the agent in `trace` fell short?",
        criteria={"wrong_tool": None, "hallucinated_fact": None, "ignored_instruction": None,
                  "gave_up": None, "none": "The run achieved the user's goal"},
    ),
    "severity": Score(
        instructions="If the run in `trace` went wrong, how much harm did it cause the user?",
        criteria=["No harm", "Wasted time, recoverable", "Wrong action taken or data lost"],
    ),
}
```

## Field lessons

- Per-field or per-flaw judges beat one holistic judge ("is this good?" gave 0.56 where per-field checks gave 0.95 and 0.85).
- Treat the questions like code: YAML or constants, versioned, with a gold set and an agreement metric. Before shipping a wording change, diff which rows flip.
- Measured: 85.9% agreement with a frontier judge at 13.6× the speed and 2.7× less cost. Another benchmark placed Jev 2nd of 7, but only 6.3× faster than Sonnet 5.
- Jev as a judge for code review is weak. Use it to triage and prioritize (P0/P1/P2), not as the final verdict.
- Offline evals: pin the model version, because the `jev-latest` alias moves.

