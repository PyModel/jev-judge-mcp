# Shape: Embedding Jev in Existing Infrastructure

**Use when** you want Jev to feel native inside a host system: SQL, a vector DB, a web framework, CI, an agent framework, a home-automation hub, a browser.

## The shape

Wrap one `system_one` call as the host's own primitive:

| Host | Native primitive | Example |
|---|---|---|
| SQL database | predicate / scalar function | `WHERE jev(row, 'the name is European')` |
| Vector DB / search | reranker class | `TypeSafeReranker` |
| Web framework | router / middleware | Jev picks the handler |
| Agent framework | middleware / guardrail hook / block | tool-call gate, model router |
| CI / git | hook / plugin / lint rule | commit, migration, and semantic lint checks |
| Home automation | sensor / automation action / conversation agent | answers as entities |
| Browser | extension content script | per-element or per-post labels |
| Test runner | plugin | test-claim checks |

```python
# Sketch: a SQLite scalar function (per-row call; cache aggressively, filter in SQL first)
import functools, json, sqlite3
from typesafe_sdk import Noul, TypeSafeClient

client = TypeSafeClient()

@functools.lru_cache(maxsize=100_000)
def _jev(row_json: str, predicate: str) -> float:
    r = client.system_one(state={"row": json.loads(row_json)},
                          questions={"p": Noul(instructions=f"Is this true of `row`: {predicate}")})
    return r.nouls["p"].noul

db = sqlite3.connect("app.db")
db.create_function("jev", 2, _jev, deterministic=True)
# SELECT * FROM people WHERE country = 'DE' AND jev(json_object('name', name), 'the name is European') > 0.7
```

## Field lessons

- A per-row call is one HTTP request per row. Filter with ordinary SQL first, cache results, and batch where the host allows it (many rows as questions over one state, within the 32k limit).
- Mark the function deterministic only with a pinned model version. The alias moves.
- In any client-side host (browser, mobile), the API key is exposed. Proxy through a server. The JS SDK needs `dangerouslyAllowBrowser` for a reason.
- Home automation users push back on cloud round-trips. Offer a local fallback.
- The strongest adoption signal is Jev inside established OSS behind a feature flag or optional provider, not Jev-first products.

