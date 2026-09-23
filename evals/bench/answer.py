"""The scored answer: the agent's final line `{"answer": ...}`, parsed, checked, and scored.

`parse` returns the answer or the reason it is unusable; an unusable answer scores as incorrect.
`is_correct` compares it with the item's `accept` list (rerank: its top-1). `wrap` shapes it into the
fields the tool's existing scorer reads, so per-tool label metrics run on agent answers unchanged
(`analysis.per_tool`); fields only Jev produces (probabilities, confidence) stay absent. Screen's
primary metric sweeps the injection probability, so it is structurally null for agent answers.
"""

import json
from typing import Any, cast

from evals.bench.items import NONE_OPTION, NOT_STATED, Item
from evals.scorers.fields import Json, as_objects

Answer = str | tuple[str, ...]


def _final_line(text: str) -> str | None:
    lines = [line.strip().strip("`").strip() for line in text.strip().splitlines()]
    lines = [line for line in lines if line]
    return lines[-1] if lines else None


def parse(item: Item, text: object) -> tuple[Answer | None, str | None]:
    """`(answer, None)`, or `(None, reason)` when the final line is missing, malformed, or off-vocabulary."""
    line = _final_line(text) if isinstance(text, str) else None
    if line is None:
        return None, "no final answer line"
    try:
        body = json.loads(line)
    except json.JSONDecodeError:
        return None, "final line is not JSON"
    if not isinstance(body, dict) or set(cast(dict[str, Any], body)) != {"answer"}:
        return None, 'final line is not {"answer": ...}'
    value = cast(dict[str, Any], body)["answer"]
    if item.tool == "jev_rerank":
        return _ranking(item, value)
    if not isinstance(value, str) or not value:
        return None, "answer is not a non-empty string"
    if item.options is not None and value not in item.options:
        return None, f"answer {value!r} is not an option"
    return value, None


def _ranking(item: Item, value: object) -> tuple[Answer | None, str | None]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in cast(list[object], value)):
        return None, "answer is not a list of ids"
    ranking = tuple(cast(list[str], value))
    if not ranking or len(set(ranking)) != len(ranking) or not set(ranking) <= set(item.options or ()):
        return None, "answer is not a duplicate-free ranking of candidate ids"
    return ranking, None


def is_correct(item: Item, answer: Answer | None) -> bool:
    if answer is None:
        return False
    top = answer[0] if isinstance(answer, tuple) else answer
    return top in item.accept


def wrap(item: Item, answer: Answer | None) -> Json:
    """`answer` in the result shape the tool's scorer reads; an unusable answer is an empty result."""
    if answer is None:
        return {}
    if isinstance(answer, tuple):
        return {"ranked": [{"id": candidate} for candidate in answer]}
    match item.tool:
        case "jev_verify":
            return {"results": [{"id": "claim0", "verdict": answer}]}
        case "jev_screen":
            return {"recommendation": {"action": "block" if answer == "injection" else "pass"}}
        case "jev_find":
            if answer == NONE_OPTION["jev_find"]:
                return {"top": [], "exists_verdict": "absent"}
            return {"top": [{"id": answer}], "exists_verdict": "answered"}
        case "jev_classify":
            item_id = as_objects(item.input["items"])[0].get("id", "0")
            return {"results": [{"id": item_id, "classification": answer}]}
        case "jev_decide":
            escaped = answer == NONE_OPTION["jev_decide"]
            return {"recommendation": {"escaped": escaped, **({} if escaped else {"selected": answer})}}
        case "jev_compare":
            return {"overall": {"relation": answer}}
        case "jev_extract":
            field_id = as_objects(item.input["fields"])[0].get("id")
            stated = answer != NOT_STATED
            return {
                "results": [
                    {"id": field_id, "value": answer if stated else None, "status": "auto" if stated else "not_found"}
                ]
            }
        case "jev_review":
            return {"action": "auto" if answer == "apply" else "review"}
        case "jev_gate":
            return {"action": "auto" if answer == "accept" else "review"}
        case _:
            raise ValueError(f"no answer shape for {item.tool}")
