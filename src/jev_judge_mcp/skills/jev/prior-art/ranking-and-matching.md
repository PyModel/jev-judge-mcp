# Shape: Ranking, Matching, and Walking

**Use when** you must order candidates, decide whether two records are the same, or navigate a graph or taxonomy.

## The shapes

**Rerank**: retrieve a shortlist in code (BM25, vectors), then ask a Noul per (query, candidate) and sort by `.noul`. The same value also prunes: drop anything below a threshold.

**Match / dedupe**: put both records in one state, then ask a Score with outcome levels (different / maybe / same) plus field-level Nouls. Route to the nearest level.

**Walk**: at each node, a Choice over the children or outgoing edges, plus a "goal reached?" Noul. Beam search keeps the top K paths.

```python
import math
from typesafe_sdk import Choice, Noul, TypeSafeClient

def walk(client: TypeSafeClient, doc: str, tree: dict, k: int = 3) -> list[tuple[list[str], float]]:
    beams = [([], 0.0)]                                    # (path, sum of log p)
    while any(_children(tree, p) for p, _ in beams):
        nxt = []
        for path, lp in beams:
            kids = _children(tree, path)
            if not kids:
                nxt.append((path, lp)); continue
            keys = {f"c{i}": name for i, name in enumerate(kids)}
            r = client.system_one(
                state={"document": doc},
                questions={"next": Choice(
                    instructions="Which direct child category best matches `document`?",
                    criteria={key: {"category": name, "subtree": kids[name]}  # the key hides the name
                              for key, name in keys.items()},
                )},
            )
            for key, p in r.choices["next"].probabilities.items():
                if p > 0:
                    nxt.append((path + [keys[key]], lp + math.log(p)))
        beams = sorted(nxt, key=lambda b: b[1] / max(len(b[0]), 1), reverse=True)[:k]
    return beams
```

`_children(tree, path)` returns a dict of `{child_name: subtree_or_description}`. The beam ranks paths by the geometric mean of their probabilities.

## Field lessons

- Rerank on a shortlist, never on the whole corpus. BM25 top-30 + a Noul per pair lifted top-10 hits from 38% to 62% for $0.0645 per 1,200 pairs.
- Ask several questions per pair in the same call (relevant, contains evidence, contradicts, injection).
- For entity resolution, block first (cheap keys), then pair-score. Keep numeric comparisons (ABV, dates, amounts) in code.
- Score levels named as outcomes remove the need for a threshold: `OUTCOME[round(score)]`.
- Beam (K=3) beat greedy 4/4 vs. 2/4 on the taxonomy walk. Showing subtrees in the option descriptions lets the model see what lives under a branch.
- Choice probabilities are relative. For "is anything relevant at all?", add an absolute Noul.

