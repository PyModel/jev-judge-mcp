"""The frozen input caps, typed per tool (ADR-0014); the manifest is the oracle, not the source.

Every value is transcribed from `docs/reference/parity-manifest.json` `caps`, and
`tests/contract/test_limits.py` fails when the two disagree, so a re-freeze points here. VALUES
ONLY: UTF-16 measurement stays in `text.py` (ADR-0005) and applying a cap — reject is the schema
itself — lives in the tools and `validation/caps.py`. A tool that hardcodes one of these numbers is
a bug.

Text bounds are UTF-16 code units (JS `.length`); `None` marks a cap the reference deliberately
leaves open (the manifest records it as null: "do not add a bound"). The three behaviors of the
manifest prose: schema `min*`/`max*` are reject, `*_units` are truncate-to-cap or skip thresholds,
and the aggregate budgets are strict greater-than errors (the cap itself is allowed).

`jev_extract`'s candidate caps (`candidates_per_field`, `candidate_units`) reach the matcher from
here, carried in each executor job; `regex_timeout_ms` is enforced in `extract/candidates.py`, which
keeps its own copy, and the contract test fails on drift between the two.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class CandidatesCaps:
    """`candidatesSchema` (`index.ts:105-116`), shared by jev_find and jev_rerank."""

    min_items: int
    max_items: int
    text_units: int


@dataclass(frozen=True, slots=True)
class VerifyCaps:
    """jev_verify: only presence is capped; lengths are deliberately open (manifest nulls)."""

    claims_min: int
    claims_max: int | None
    claim_units: int | None
    evidence_min: int
    evidence_max: int | None


@dataclass(frozen=True, slots=True)
class ScreenCaps:
    """jev_screen: `text` only needs content; no length bound (manifest nulls)."""

    text_min: int
    text_max: int | None
    purpose_max: int | None


@dataclass(frozen=True, slots=True)
class FindCaps:
    """jev_find: candidate bounds are the shared `CANDIDATES`; `query_min` is a schema-only bound
    the manifest does not record (its `jev_find` caps block has no `query` field), so the tie is
    `tests/contract/test_limits.py::test_schema_only_caps_stay_owned_by_limits`, not the manifest."""

    top_k_min: int
    top_k_max: int
    top_k_default: int
    query_min: int


@dataclass(frozen=True, slots=True)
class ClassifyCaps:
    """jev_classify: per-entry text truncation plus the item-class budget error."""

    items_min: int
    items_max: int
    item_units: int
    classes_min: int
    classes_max: int
    class_description_units: int
    item_class_pairs: int


@dataclass(frozen=True, slots=True)
class DecideCaps:
    """jev_decide: every bound is a schema reject; nothing is truncated."""

    decision_min: int
    decision_max: int
    evidence_min: int
    evidence_max: int
    priorities_min: int
    priorities_max: int
    candidates_min: int
    candidates_max: int
    candidate_id_max: int
    candidate_description_min: int
    candidate_description_max: int
    requirements_min: int
    requirements_max: int
    requirement_min: int
    requirement_max: int


@dataclass(frozen=True, slots=True)
class RerankCaps:
    """jev_rerank: candidate bounds are the shared `CANDIDATES`."""

    query_min: int
    query_max: int
    aggregate_candidate_units: int
    top_k_min: int
    top_k_max: int


@dataclass(frozen=True, slots=True)
class CompareCaps:
    """jev_compare: passages are schema-rejected above the cap; the truncate call is a no-op guard."""

    passage_min: int
    passage_max: int
    aspects_min: int
    aspects_max: int
    aspect_min: int
    aspect_max: int


@dataclass(frozen=True, slots=True)
class ExtractCaps:
    """jev_extract: schema rejects, one aggregate budget error, and the worker's pipeline caps."""

    document_min: int
    document_max: int
    fields_min: int
    fields_max: int
    field_id_max: int
    pattern_min: int
    pattern_max: int
    flags_max: int
    description_min: int
    description_max: int
    candidates_per_field: int
    candidate_units: int
    aggregate_candidate_units: int
    regex_timeout_ms: int


@dataclass(frozen=True, slots=True)
class ReviewCaps:
    """jev_review: request, diff, and tests are each truncated at the doc cap, never schema-rejected."""

    doc_units: int


@dataclass(frozen=True, slots=True)
class GateCaps:
    """jev_gate: claim/item/text caps and the two aggregate isError budgets."""

    claims_min: int
    claims_max: int
    claim_units: int
    evidence_items: int
    aggregate_evidence_units: int
    doc_units: int


@dataclass(frozen=True, slots=True)
class ScoreCaps:
    """jev_score (extension tool, ADR-0048): no parity-manifest block exists, so the ADR owns every value.

    Levels follow the documented score question space (`docs/jev_docs/primitives.md`: 1-10 levels,
    at least 2 preferred; `ScoreQuestion` itself rejects fewer than two before sending). Text bounds
    mirror the house scale for judgment inputs: a decide-sized subject budget, a class-description
    scale per level, presence-only for the optional context.
    """

    levels_min: int
    levels_max: int
    level_units_min: int
    level_units_max: int
    subject_min: int
    subject_max: int
    context_max: int


CANDIDATES: Final = CandidatesCaps(min_items=1, max_items=250, text_units=2000)
"""jev_find `"candidates": "1..250 reject"` / `"candidate_text": "2000 truncate"`; jev_rerank
`"candidates": "..250 reject"` — one shared schema and one shared truncation."""

VERIFY: Final = VerifyCaps(claims_min=1, claims_max=None, claim_units=None, evidence_min=1, evidence_max=None)
SCREEN: Final = ScreenCaps(text_min=1, text_max=None, purpose_max=None)
FIND: Final = FindCaps(top_k_min=1, top_k_max=50, top_k_default=5, query_min=1)

SANITIZE_ID_UNITS: Final = 64
"""The reference truncates a sanitized id at 64 UTF-16 units (`lib.ts` `sanitizeId`): a Python-side
input cap the manifest does not record. It is not `DECIDE.candidate_id_max` or `EXTRACT.field_id_max`
— same number, different cap — so `ids.py` imports this one."""
CLASSIFY: Final = ClassifyCaps(
    items_min=1,
    items_max=64,
    item_units=2000,
    classes_min=2,
    classes_max=250,
    class_description_units=2000,
    item_class_pairs=8_000,
)
DECIDE: Final = DecideCaps(
    decision_min=1,
    decision_max=1500,
    evidence_min=1,
    evidence_max=12_000,
    priorities_min=1,
    priorities_max=2000,
    candidates_min=2,
    candidates_max=6,
    candidate_id_max=64,
    candidate_description_min=1,
    candidate_description_max=2000,
    requirements_min=0,
    requirements_max=3,
    requirement_min=1,
    requirement_max=500,
)
RERANK: Final = RerankCaps(query_min=1, query_max=2000, aggregate_candidate_units=100_000, top_k_min=1, top_k_max=250)
COMPARE: Final = CompareCaps(
    passage_min=1, passage_max=20_000, aspects_min=0, aspects_max=10, aspect_min=1, aspect_max=200
)
EXTRACT: Final = ExtractCaps(
    document_min=1,
    document_max=50_000,
    fields_min=1,
    fields_max=32,
    field_id_max=64,
    pattern_min=1,
    pattern_max=500,
    flags_max=8,
    description_min=1,
    description_max=2000,
    candidates_per_field=20,
    candidate_units=2000,
    aggregate_candidate_units=50_000,
    regex_timeout_ms=1000,
)
REVIEW: Final = ReviewCaps(doc_units=50_000)
GATE: Final = GateCaps(
    claims_min=1,
    claims_max=16,
    claim_units=2000,
    evidence_items=16,
    aggregate_evidence_units=200_000,
    doc_units=50_000,
)
SCORE: Final = ScoreCaps(
    levels_min=2,
    levels_max=10,
    level_units_min=1,
    level_units_max=200,
    subject_min=1,
    subject_max=1500,
    context_max=12_000,
)
