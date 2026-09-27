# Shape: Stream Filters and Bulk Labelling

**Use when** you must label, filter, or route every item in a large or endless flow: social posts, emails, logs, rows, comments, DOM elements, headlines, applications.

## The shape

```
for each item (bounded concurrency, about 8 workers):
    one request: several speculative questions about this item
    code: threshold / label / drop / route; store the raw probabilities
```

```python
from concurrent.futures import ThreadPoolExecutor
from typesafe_sdk import Choice, Noul, TypeSafeClient

client = TypeSafeClient()
QUESTIONS = {
    "ai_written": Noul(instructions="Does `post` read as AI-generated filler rather than a person's own words?"),
    "promotional": Noul(instructions="Is `post` primarily promoting a product, service, or the author?"),
    "topic": Choice(instructions="What is `post` mainly about?",
                    criteria={"work": None, "tech": None, "politics": None, "personal": None, "other": None}),
}

def label(post: str) -> dict:
    r = client.system_one(state={"post": post}, questions=QUESTIONS)
    return {"ai": r.nouls["ai_written"].noul, "promo": r.nouls["promotional"].noul,
            "topic": r.choices["topic"].choice, "model": r.model}

with ThreadPoolExecutor(max_workers=8) as pool:
    labels = list(pool.map(label, posts))
hidden = [p for p, l in zip(posts, labels) if max(l["ai"], l["promo"]) > 0.8]
```

## Variants

- **User-tuned weights**: store the raw probabilities and let the user's own labels fit the weights or thresholds (slop-filter).
- **Per-DOM-element**: "is this element an ad?" in a browser extension. Batch many elements per request, one Noul per element, where one page is the state.
- **Log and code grep by meaning**: pre-filter with a regex or line window, then ask per line or chunk.
- **Bulk offline jobs**: resumes, papers, reviews, applications. Cache on (state, questions, model).
- **Periodic feeds**: poll headlines every N minutes and score them.

## Field lessons

- About 8 concurrent workers is the practical ceiling on a shared key before 429s.
- Cost references: about $0.00003 per social post; 500 emails for 3.5¢; 1M log lines for about $10 in about 10 minutes; 1,018 papers for 8¢; 724 ads in 40 s for 9¢; 3,518 applications in under 5 minutes.
- Put many questions in each request. The state (the item) dominates tokens, so extra questions are almost free.
- Take a hard look at "is this AI-written?" questions. They are vibes-level judgments. Calibrate on your own labels before hiding content.
- For offline bulk work, batched LLM prompts (20 records per call) can match Jev on cost. Jev wins on latency and per-item isolation.

