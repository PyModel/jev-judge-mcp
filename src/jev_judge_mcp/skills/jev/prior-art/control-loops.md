# Shape: Control Loops

**Use when** something must act repeatedly on changing state: games, simulations, robots, drones, vehicles, trading, device automation.

## The shape

```
loop every tick:
    observe  → code turns raw state into compact text/JSON (RAM, CV, order book, a11y tree)
    decide   → one request: a Choice over the LEGAL actions, plus speculative side questions
    act      → code executes: pathing, flight control, order placement, input injection
    verify   → code checks the effect; failures feed the next observation
```

Code owns physics, pathing, math, risk limits, and the action set. Jev only answers "what does this situation call for?".

```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

client = TypeSafeClient()  # one client for the whole loop

def decide(obs: dict, legal: list[str]) -> str | None:
    r = client.system_one(
        state={"observation": obs},
        questions={
            "action": Choice(
                instructions="Given `observation`, which action best advances the goal?",
                criteria={a: None for a in legal},     # only legal actions; rebuild every tick
            ),
            "danger": Score(
                instructions="How much immediate danger is the agent in?",
                criteria=["No threat nearby", "Threat present but not engaging",
                          "Taking damage or about to"],
            ),
            "target_lost": Noul(instructions="Is the tracked target no longer visible in `observation`?"),
        },
    )
    if r.choices["action"].confidence < 0.4:
        return None                                    # hold, or escalate to a planner
    return r.choices["action"].choice
```

## Variants

- **Hierarchical**: Jev picks a goal ("go to Pewter City"), code pathfinds (A*). Jev only takes the branch points. This is cheaper and more robust than per-button control.
- **Planner + actor**: an LLM sets a goal every N ticks, and Jev picks macro-actions each tick. See `llm-pairing.md`.
- **Objective ladder**: code keeps a fixed curriculum (wood → stone → iron → diamond). Jev picks among the actions for the current rung.
- **Tutor → student**: log Jev's decisions from successful episodes and train a small local policy that gradually takes over. The loop gets cheaper the longer it runs.
- **Multi-channel**: separate questions per subsystem (navigation vs. combat, direction vs. regime vs. inventory) in the same request.

## Field lessons

- Put **objects**, not pixels or raw bytes, in the state: object-centric JSON from RAM, numbered UI elements, a range-sector table from CV. Jev is text-only and weak on raw numbers.
- Keep what code observed apart from what Jev inferred. An inferred label is not a fact. Before acting on an answer, check that the state it came from still holds. A decision about a stale observation is a decision about a different situation.
- Rebuild the Choice options every tick from what is actually legal. An illegal option wastes probability mass and invites bad actions.
- Tick rates people hit: about 2.5 Hz (drone), about 5 Hz (4 questions every 200 ms, driving), about 10 Hz (Doom), about 300 ms per block (on-chain trading). Per-decision latency is about 80–300 ms.
- Search-heavy games (chess) fail. Reflex and situational games (Doom, Mario, Pong, Minecraft survival) work.
- For money, Jev proposes and a deterministic risk gate disposes. Nobody lets Jev place orders unguarded. Use dry-run defaults.
- Cost reference: Doom about $7/hour at 10 Hz. Minecraft about 1¢ per 2 minutes. Pokémon Red took 4 badges for under $0.50.

