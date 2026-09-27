# Shape: Select From Candidates

**Use when** the answer already exists somewhere: a UI element, a tool, a function argument value, a span of text, a model tier, a handler. Jev picks it and code copies it. Jev never generates.

## The shape

```
enumerate  → code lists candidates (DOM/a11y refs, OCR boxes, regex hits, tool registry, Literal values)
select     → Choice with the candidates as keys (+ "none"), plus an absolute Noul ("does any fit?")
execute    → code uses the exact selected string or object
```

```python
from typesafe_sdk import Choice, Noul, TypeSafeClient

def pick(client: TypeSafeClient, goal: str, elements: list[dict]) -> dict | None:
    table = {f"e{i}": el for i, el in enumerate(elements)}       # opaque keys, details in state
    r = client.system_one(
        state={"goal": goal, "elements": table},
        questions={
            "target": Choice(
                instructions="Which element in `elements` should be used next to achieve `goal`?",
                criteria={k: None for k in table} | {"none": "No element helps with the goal"},
            ),
            "destructive": Noul(instructions="Would acting on the most relevant element delete, "
                                             "pay, send, or otherwise be hard to undo?"),
        },
    )
    t = r.choices["target"]
    if t.choice == "none" or t.confidence < 0.5 or r.nouls["destructive"].noul > 0.5:
        return None                                              # ask a human or escalate
    return table[t.choice]
```

## Variants

- **Operation + target in one Choice**: keys like `click:e12`, `type:e4`, `scroll:down`. One call per step.
- **Function calling with no LLM**: a `__tool__` Choice picks the function. Each `Literal` argument becomes a Choice keyed by the exact accepted strings, each `list[Literal]` one Noul per member, each `bool` a Noul. A `stated` Noul per argument ("did the user say anything about this?") keeps defaults. Code builds any text reply. Put all questions in one request (54 in the cookbook).
- **Value extraction**: an over-eager regex finds candidates. The candidate strings are the Choice keys, plus `none`. No transposed digits, no invented values.
- **Routers**: the candidates are models, effort levels, skills, MCP tools, or HTTP handlers.
- **Large rosters**: up to 255 options per Choice. Above that, pre-rank with a Score or Noul, or use two stages (short descriptions → top 3 with full text).

## Field lessons

- Give opaque keys (`e0`, `c3`) and put the details in state or in the option values. Where the exact string is the payload (extraction, `Literal` args), use the string itself as the key.
- A Choice always has a winner. Pair it with an absolute Noul (`exists`, `fits`, `stated`), or add a `none` key.
- Numbered element tables beat screenshots: $0.0002–$0.004 per step and 7 s flight bookings, against minutes and dollars for vision agents.
- When a free-text value is needed (a search query, a message body), call a small LLM for that one field only.
- A distilled 706k-parameter specialist beat hosted Jev on form filling (99.7% vs. 83.6%, 7–9 ms). At high volume on one narrow task, consider distilling.

