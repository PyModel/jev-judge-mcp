# Shape: Agent Context, Memory, and Effort

**Use when** you are building harness plumbing for an LLM agent: what stays in context, what gets remembered, when memories expire, how much reasoning effort to spend, which skill or tool to surface.

## The shape

Replace generative steps (summarize, rewrite) with **per-item decisions**. The kept content stays verbatim.

```
compaction : for each context block → Choice keep / truncate / drop, given the current task
memory     : after each turn → Noul "anything worth remembering?" → Choice category → append verbatim
lease      : for each stored memory → Noul "does the new evidence invalidate this?"
effort     : each step → Score "how stuck / how hard is this step?" → map to reasoning effort or model tier
```

```python
from typesafe_sdk import Choice, TypeSafeClient

def compact(client: TypeSafeClient, task: str, blocks: list[str]) -> list[str]:
    qs = {
        f"b{i}": Choice(
            instructions={"question": "What should happen to `blocks[%d]` for the rest of `task`?" % i},
            criteria={"keep": "Still needed verbatim to finish the task",
                      "truncate": "Only the first lines or the result matter now",
                      "drop": "No longer relevant to the task"},
        )
        for i in range(len(blocks))
    }
    r = client.system_one(state={"task": task, "blocks": blocks}, questions=qs)
    out = []
    for i, b in enumerate(blocks):
        a = r.choices[f"b{i}"]
        if a.choice == "keep" or a.confidence < 0.5:           # unsure → keep
            out.append(b)
        elif a.choice == "truncate":
            out.append(b[:400] + "\n[truncated]")
    return out
```

Mind the 32k-token limit on state plus the longest question. Chunk the blocks across requests for long transcripts.

## Field lessons

- A verbatim keep/drop is safer than a summary: nothing gets paraphrased wrong, and the decision is auditable.
- Changing the model or effort mid-session can invalidate the prompt/KV cache and erase the savings. Keep the tier stable within a cacheable span.
- Memory gates hit 98.5% save/skip accuracy at 0.3 s per decision.
- An effort governor can cut costs by about 50% with the cache preserved.
- Default to keep, or to "remember nothing", when confidence is low. False drops hurt more than extra tokens.

