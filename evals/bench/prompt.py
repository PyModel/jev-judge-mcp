"""What the agent sees: the bench system addendum and one item rendered as a user message.

Every arm gets the same user message. A (direct) and B (automatic) get the same addendum. C, the
forced arm, adds one sentence, which is the treatment. The renderer reads only the item's `input`,
`question`, and `options`, so authoring metadata (`intended`, `source`, `label`) can never reach the
agent.
"""

from collections.abc import Callable
from typing import Any, cast

from evals.bench.items import NONE_OPTION, NOT_STATED, Item
from evals.scorers.fields import Json, as_objects

BENCH_ADDENDUM = (
    "You are answering one judgment question. Everything you need is in the user's message. "
    "Decide the answer from that material, then end your reply with the final answer line exactly "
    "as the message specifies."
)
"""Shared by every arm, identical on A and B, and it never names Jev (ADR-0036). The earlier wording
also said "do not look for files or other sources"; that sentence suppressed the very tool use arm B
exists to observe, so it is gone. The question material is still declared complete."""
JEV_SENTENCE = "Before you answer, call at least one mcp__jev__ tool on this question and use its result."
JEV_SENTENCE_PI = "Before you answer, call at least one of the jev_ tools on this question and use its result."
"""The jev server is eager and direct in the bench config, so the published tools (`jev_verify`, ...)
are already in the model's tool list: C's sentence names them, not the adapter's gateway dance."""


def addendum(arm: str, agent: str = "claude") -> str:
    """A and B share the addendum. C adds the one sentence that tells the agent to call Jev."""
    if arm != "C":
        return BENCH_ADDENDUM
    sentence = JEV_SENTENCE_PI if agent == "pi" else JEV_SENTENCE
    return f"{BENCH_ADDENDUM} {sentence}"


def _block(title: str, text: object) -> str:
    return f"{title}:\n{text}"


def _entries(value: object, text_key: str) -> str:
    return "\n".join(
        f"[{entry.get('id', index)}] {entry.get(text_key)}" for index, entry in enumerate(as_objects(value))
    )


def _evidence(value: object) -> str:
    return value if isinstance(value, str) else _entries(value, "text")


def _bullets(value: object) -> str:
    return "\n".join(f"- {line}" for line in cast(list[Any], value))


def _optional(material: Json, key: str, title: str) -> list[str]:
    return [_block(title, material[key])] if material.get(key) else []


def _verify(m: Json) -> list[str]:
    return [_block("Claim", cast(list[str], m["claims"])[0]), _block("Evidence", _evidence(m["evidence"]))]


def _screen(m: Json) -> list[str]:
    content = f"----- BEGIN CONTENT -----\n{m['text']}\n----- END CONTENT -----"
    return [*_optional(m, "purpose", "What the reader of this content is trying to do"), _block("Content", content)]


def _ranked(m: Json) -> list[str]:
    return [_block("Query", m["query"]), _block("Candidates", _entries(m["candidates"], "text"))]


def _classify(m: Json) -> list[str]:
    classes = "\n".join(f"- {c.get('id')}: {c.get('description')}" for c in as_objects(m["classes"]))
    return [
        *_optional(m, "purpose", "Purpose"),
        *_optional(m, "context", "Context"),
        _block("Classes", classes),
        _block("Item", as_objects(m["items"])[0].get("text")),
    ]


def _decide(m: Json) -> list[str]:
    candidates = "\n".join(f"- {c.get('id')}: {c.get('description')}" for c in as_objects(m["candidates"]))
    requirements = [_block("Requirements", _bullets(m["requirements"]))] if m.get("requirements") else []
    return [
        _block("Decision", m["decision"]),
        _block("Evidence", m["evidence"]),
        _block("Priorities", m["priorities"]),
        _block("Candidates", candidates),
        *requirements,
    ]


def _compare(m: Json) -> list[str]:
    return [
        *_optional(m, "purpose", "Purpose"),
        _block("Passage A", m["passage_a"]),
        _block("Passage B", m["passage_b"]),
    ]


def _extract(m: Json) -> list[str]:
    field = as_objects(m["fields"])[0]
    return [
        *_optional(m, "purpose", "Purpose"),
        _block("Document", m["document"]),
        _block("Field", f"{field.get('id')}: {field.get('description')}"),
        _block("Candidate pattern (JavaScript regex source)", field.get("pattern")),
    ]


def _review(m: Json) -> list[str]:
    return [_block("Request", m["request"]), _block("Diff", m["diff"]), *_optional(m, "tests", "Reported tests")]


def _gate(m: Json) -> list[str]:
    claims = "\n".join(f"{index + 1}. {claim}" for index, claim in enumerate(cast(list[str], m["claims"])))
    return [
        _block("Request", m["request"]),
        _block("Diff", m["diff"]),
        _block("Completion claims", claims),
        _block("Evidence", _evidence(m["evidence"])),
        *_optional(m, "tests", "Reported tests"),
    ]


MATERIAL: dict[str, Callable[[Json], list[str]]] = {
    "jev_verify": _verify,
    "jev_screen": _screen,
    "jev_find": _ranked,
    "jev_classify": _classify,
    "jev_decide": _decide,
    "jev_rerank": _ranked,
    "jev_compare": _compare,
    "jev_extract": _extract,
    "jev_review": _review,
    "jev_gate": _gate,
}


def _codes(options: tuple[str, ...]) -> str:
    return ", ".join(f"`{option}`" for option in options)


def answer_format(item: Item) -> str:
    if item.tool == "jev_rerank":
        return (
            f"Candidate ids: {_codes(item.options or ())}. End your reply with one line of JSON and nothing after it: "
            '{"answer": ["<id>", ...]}, listing every candidate id once, most relevant first.'
        )
    if item.tool == "jev_extract":
        return (
            "End your reply with one line of JSON and nothing after it: "
            '{"answer": "<the value exactly as written in the document>"}, or '
            f'{{"answer": "{NOT_STATED}"}} if the document does not state it.'
        )
    none = f" Answer `{NONE_OPTION[item.tool]}` if no candidate fits." if item.tool in NONE_OPTION else ""
    return (
        f"Options: {_codes(item.options or ())}.{none} End your reply with one line of JSON and nothing after it: "
        '{"answer": "<one option>"}.'
    )


def render(item: Item) -> str:
    sections = [*MATERIAL[item.tool](item.input), _block("Question", item.question), answer_format(item)]
    return "\n\n".join(sections)
