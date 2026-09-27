# Shape: Pairing Jev With an LLM

**Use when** part of the job needs generation, long reasoning, or perception, and part is a frequent, fast judgment. Split them.

## The shapes

| Pairing | Who does what | Use for |
|---|---|---|
| **Planner / actor** | An LLM sets a goal every N steps. Jev picks actions every step. | Games, robots, long-horizon agents |
| **Verify-then-escalate cascade** | A cheap LLM produces. Jev checks each field or claim. Flagged items go to a strong LLM. | Extraction, answers, citations |
| **Front-door router** | Jev classifies intent and complexity. It routes to code, a specialist LLM, or a human. | Support, assistants |
| **Proposer / selector** | An LLM or CV proposes candidates. Jev selects. | Extraction, detection boxes, art |
| **Jev decides, LLM writes** | Jev picks the tool, arguments, and branch. A small LLM writes only the free-text field. | Tool calling, replies |
| **Rule writer / runner** | An LLM rewrites the rules or questions offline. Jev runs them live. | Trading bots, prompt → rubric migration |
| **Tutor → student** | Jev's decisions train a small local model that takes over | High-volume narrow loops |
| **Trigger → investigator** | Jev decides "is this serious?". An LLM digs into logs and writes a report. | Monitoring, SOC |

```python
from typesafe_sdk import Noul, TypeSafeClient

FIRE = 0.7
FLAWS = {
    "hallucinated": "Is `extracted_value` absent from `source_text`?",
    "off_target": "Does `extracted_value` answer a different field than `field_spec` asks for?",
    "format_violation": "Does `extracted_value` break the format in `field_spec`?",
}

def verify(client: TypeSafeClient, source: str, schema: dict, record: dict) -> bool:
    qs = {
        f"{field}::{flaw}": Noul(instructions={
            "field_spec": schema[field], "extracted_value": value, "question": text})
        for field, value in record.items() for flaw, text in FLAWS.items()
    }
    r = client.system_one(state={"source_text": source}, questions=qs)
    return max(a.noul for a in r.nouls.values()) > FIRE      # True → escalate to the strong LLM
```

## Field lessons

- Escalate on the **maximum** flag, not the average.
- Hallucination checks read literally. A reformatted value ("03/14/2026" against the source's "March 14, 2026") scored 0.84 "absent" in a live test. Normalize values before the check, or ask "Is the value in `extracted_value` stated in `source_text`, in any format?".
- Put Jev on the hot path (every tick, every item) and the LLM on the cold path (rare, hard).
- The route "define with an LLM → run in Jev → train your own classifier on the collected labels" came up repeatedly. Jev is a middle stage, not always the end state.
- An LLM ensemble can generate labels when you have none, for tuning thresholds or training a downstream model.

