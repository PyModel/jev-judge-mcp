"""Schema fragments and helpers shared by several tools (`index.ts:88-116`, `lib.ts:363-374`)."""

from collections.abc import Mapping
from typing import Any, cast

from jev_judge_mcp.ids import ensure_unique_ids
from jev_judge_mcp.limits import CANDIDATES, VERIFY

EVIDENCE_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "string", "description": "A single evidence document."},
        {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Short identifier for this evidence item."},
                "text": {"type": "string", "description": "The evidence text."},
            },
            "required": ["text"],
            "additionalProperties": False,
            "description": "A single evidence item.",
        },
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Short identifier for this evidence item (e.g. 'site-html', 'rfc-4.1.3').",
                    },
                    "text": {"type": "string", "description": "The evidence text."},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            "minItems": VERIFY.evidence_min,
            "description": "Multiple evidence items; each claim is also matched to the item it rests on.",
        },
    ]
}
"""`evidenceSchema` (`index.ts:88-103`): one string, one `{id?, text}`, or a non-empty array of them."""


def candidates_schema(description: str) -> dict[str, Any]:
    """`candidatesSchema` (`index.ts:105-116`), shared by jev_find and jev_rerank."""
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Short identifier for this candidate (e.g. a file path, note name, or line id).",
                },
                "text": {"type": "string", "description": "The candidate's text."},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        "minItems": CANDIDATES.min_items,
        "maxItems": CANDIDATES.max_items,
        "description": description,
    }


def evidence_items(raw: object) -> list[dict[str, object]]:
    """Parsed `evidence` as a list of `{id?, text}` items: a string is one item with id `evidence`."""
    if isinstance(raw, str):
        return [{"id": "evidence", "text": raw}]
    if isinstance(raw, list):
        return cast(list[dict[str, object]], raw)
    return [cast(dict[str, object], raw)]


def normalize_evidence(raw: object) -> list[dict[str, object]]:
    """`normalizeEvidence` (`lib.ts:363-368`): evidence items with safe, unique ids."""
    return ensure_unique_ids(evidence_items(raw), "evidence").items


_JS_WHITESPACE = "".join(
    map(chr, (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F))
) + "".join(map(chr, (0x205F, 0x3000, 0xFEFF)))
"""ECMAScript WhiteSpace and LineTerminator, what `String.prototype.trim` strips. Python's `strip()` differs."""


def js_trim(text: str) -> str:
    return text.strip(_JS_WHITESPACE)


def has_non_empty_evidence(items: list[dict[str, object]]) -> bool:
    """`hasNonEmptyEvidence` (`lib.ts:371-374`)."""
    return any(js_trim(str(item["text"])) for item in items)


def text_of(item: Mapping[str, object]) -> str:
    return str(item["text"])
