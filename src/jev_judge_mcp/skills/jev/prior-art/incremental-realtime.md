# Shape: Incremental and Real-Time Decisions

**Use when** the input arrives in pieces and you must decide before it is complete: speech transcripts, keystrokes, video captions, sensor feeds, live translation.

## The shape

```
on each increment (word, keystroke batch, caption segment):
    one request over the input so far: "is it complete enough?", "what is it?", "is it risky?"
    commit when confidence crosses a bar, or when a max hold time expires; otherwise wait
```

```python
from typesafe_sdk import Choice, Noul, TypeSafeClient

COMMIT_AT = 0.85
MAX_HOLD_S = 17.0

def on_partial(client: TypeSafeClient, text_so_far: str, held_for_s: float, intents: dict) -> str | None:
    r = client.system_one(
        state={"utterance_so_far": text_so_far},
        questions={
            "complete": Noul(instructions="Is `utterance_so_far` a complete thought that can be acted on now?"),
            "addressed_to_me": Noul(instructions="Is `utterance_so_far` addressed to the assistant?"),
            "intent": Choice(instructions="What does the speaker want?", criteria=intents | {"unclear": None}),
            "destructive": Noul(instructions="Would acting on `utterance_so_far` be hard to undo?"),
        },
    )
    ready = r.nouls["complete"].noul > 0.8 and r.choices["intent"].confidence >= COMMIT_AT
    if (ready or held_for_s > MAX_HOLD_S) and r.nouls["addressed_to_me"].noul > 0.7:
        if r.nouls["destructive"].noul > 0.5:
            return "confirm:" + r.choices["intent"].choice
        return r.choices["intent"].choice
    return None
```

## Field lessons

- Latency per decision is about 100–350 ms, fast enough to re-ask on every word or every 350 ms of typing.
- Always add a max-hold timeout. A dubbing app capped the hold at 17 s where an LLM approach needed 40 s.
- Ask "addressed to me?" and "destructive?" on every increment for voice. Partial speech misfires.
- Show the live probabilities in the UI (seek-bar painting, a morphing card, a top-3 bar). It makes the uncertainty legible and fun.
- Cost is small even at high frequency: dubbing runs about 2¢ per hour of audio.

