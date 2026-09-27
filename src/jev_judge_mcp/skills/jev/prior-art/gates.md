# Shape: Gates

**Use when** something must be allowed, blocked, reviewed, or escalated before it happens: agent tool calls, shell commands, commits, migrations, trades, refunds, content, agent "done" claims.

## The shape

```
proposed action + context → code rules: a known-bad pattern blocks here, and Jev never sees it
                          → one request: specific risk Nouls + a severity Score (+ category Choice)
                          → code policy: block if any serious flag, review if uncertain, else allow
                          → log the answers; re-route cached answers when the policy changes
```

```python
import re

from typesafe_sdk import Noul, NoulCriteria, Score, TypeSafeClient

POLICY = {"review": 0.35, "block": 0.70, "severity_block": 1.5}   # one place, reviewable
# Exact conditions whose miss cannot be undone. Jev cannot override these.
DENY = [re.compile(p) for p in (
    r"\brm\s+(-\S+\s+)+(/|~|\$HOME)/?\*?(\s|$)",             # rm -rf on / or home, incl. ~/ and /*
    r"\bgit\s+push\b.*(--force|-f)\b",                          # force push
    r"\b(mkfs|dd\s+if=)",                                        # overwrite a disk
    r"\bDROP\s+(TABLE|DATABASE)\b",
)]

def gate(client: TypeSafeClient, command: str, cwd: str) -> str:
    if any(p.search(command) for p in DENY):
        return "block"
    r = client.system_one(
        state={"command": command, "cwd": cwd},
        questions={
            "deletes_data": Noul(instructions="Does `command` delete or overwrite files or data?"),
            "exfiltrates": Noul(
                instructions="Does `command` send local files, secrets, or env vars to a remote host?",
                criteria=NoulCriteria(true="Uploads, posts, or pipes local content to a network destination",
                                      false="No local content leaves the machine"),
            ),
            "outside_cwd": Noul(instructions="Does `command` modify anything outside `cwd`?"),
            "severity": Score(
                instructions="If `command` went wrong, how bad would the damage be?",
                criteria=["Nothing lasting; trivially undone", "Recoverable with effort",
                          "Irreversible loss or exposure"],
            ),
        },
    )
    flags = [r.nouls[k].noul for k in ("deletes_data", "exfiltrates", "outside_cwd")]
    if max(flags) >= POLICY["block"] or r.scores["severity"].score >= POLICY["severity_block"]:
        return "block"
    if max(flags) >= POLICY["review"]:
        return "review"
    return "allow"
```

## Variants

- **Allow/ask/deny Choice**: one 3-way verdict. It is simpler, but you lose the reason and the ability to re-tune per flag.
- **Completion gate ("not done until Jev agrees")**: Stop hooks ask evidence questions ("were the tests run?", "does the diff match the stated change?") before an agent may finish.
- **Pre-action gate in finance**: Jev runs before a deterministic risk engine and before a slower LLM gate.
- **Verified cascade**: Jev checks a cheap LLM's output. Only flagged cases go to the expensive model (see `llm-pairing.md`).
- **Input and output batteries**: separate question sets for the user prompt and for the model's reply.
- **Window or burst gate** (Discord raid, fraud burst, alert storm): code computes the window stats (join rate, message rate, account ages, duplicate ratio) and samples 5–20 events into the state. Nouls ask "coordinated raid?" and "spam-bot pattern?", a Score rates severity, and the policy decides whether to lock, alert, or allow. Keep the rates and counts in code, because Jev does not count.
- **Human-in-the-loop UX**: return a Choice to the human as buttons ("Reverse Jev").

## Field lessons

- Combine flags with the **maximum**, never the average. One serious flag must win.
- Keep the policy (thresholds, precedence) in code or config. Re-running a policy on cached probabilities costs nothing.
- Phrase every flag so that yes = the bad thing. Write `true`/`false` criteria for subtle boundaries.
- Jev is not a security boundary against adversarial input. Keep untrusted text in its own field, add an injection Noul, and never let a Jev "allow" alone authorize money or deletion. Put exact rules in code before the Jev call: a denylist, a spending limit, a protected branch. A rule's block stands whatever Jev answers. Jev judges what the rules do not decide, and can only make the result stricter.
- Approvals were 8.7× faster and used 4.4× fewer prompts on 153 real commands. Command-safety review was 5–18× faster than a frontier LLM.

