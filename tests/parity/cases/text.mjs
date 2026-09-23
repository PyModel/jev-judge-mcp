// Unicode/astral text (UTF-16 lengths, split surrogates, JSON escaping) and
// duplicate or colliding ids (handler errors, suffixing, fallbacks).
import { choice, CLAIM, COMPARE, noul, ok, RELATION, REVIEW_ARGS, spread, STRONG_REVIEW } from "./_helpers.mjs";

const GRIN = "\u{1F600}";

export default [
  // ── unicode-astral ────────────────────────────────────────────────────────
  {
    id: "unicode-astral/find-truncation-splits-surrogate-pair",
    class: "unicode-astral",
    divergences: ["ADR-0005"],
    note: "Quirk Q8: slice(0, 2000) keeps the high surrogate of U+1F600, which JSON.stringify escapes as \\ud83d. Python drops the dangling unit (ADR-0005).",
    tool: "jev_find",
    arguments: {
      query: "which note ends in an emoji",
      candidates: [
        { id: "split", text: `${"a".repeat(1999)}${GRIN} tail` },
        { id: "plain", text: "short" },
      ],
    },
    responses: [ok({ best: spread("split", ["split", "plain"], 0.8, 0.7), exists: noul(0.9) })],
  },
  {
    id: "unicode-astral/verify-astral-rtl-combining-text",
    class: "unicode-astral",
    note: "Non-ASCII text is written as UTF-8 characters, not \\u escapes (ADR-0006).",
    tool: "jev_verify",
    arguments: {
      claims: [`Deploy finished ${GRIN}`, "الخدمة تعمل", "Café is open"],
      evidence: `deploy ok ${GRIN} — الخدمة تعمل — Café is open`,
    },
    responses: [
      ok({
        relation_claim0: spread("supports", RELATION, 0.9, 0.9),
        relation_claim1: spread("supports", RELATION, 0.9, 0.9),
        relation_claim2: spread("says_nothing", RELATION, 0.6, 0.5),
      }),
    ],
  },
  {
    id: "unicode-astral/extract-dot-matches-single-units",
    class: "unicode-astral",
    note: "Without u, '.' matches one UTF-16 unit, so U+1F600 yields two lone-surrogate candidates; the pick is kept and escaped (ADR-0004, ADR-0006).",
    tool: "jev_extract",
    arguments: { document: `x${GRIN}y`, fields: [{ id: "unit", pattern: ".", description: "The second code unit." }] },
    responses: [ok({ f0: choice("c1", { c0: 0.05, c1: 0.85, c2: 0.04, c3: 0.04, none_of_them: 0.02 }, 0.85) })],
  },
  {
    id: "unicode-astral/extract-quantifier-counts-units",
    class: "unicode-astral",
    note: ".{3} over 'a' + U+1F600 is one match of three units.",
    tool: "jev_extract",
    arguments: { document: `a${GRIN} yz`, fields: [{ id: "three", pattern: ".{3}", description: "The first three units." }] },
    responses: [ok({ f0: choice("c0", { c0: 0.9, c1: 0.08, none_of_them: 0.02 }, 0.9) })],
  },
  {
    id: "unicode-astral/extract-whitespace-class",
    class: "unicode-astral",
    note: "ECMAScript \\s matches U+00A0 and U+3000 but not U+0085.",
    tool: "jev_extract",
    arguments: {
      document: "A B\u0085C　D",
      fields: [{ id: "space", pattern: "\\S\\s\\S", description: "A letter pair split by whitespace." }],
    },
    responses: [ok({ f0: choice("c0", { c0: 0.9, c1: 0.08, none_of_them: 0.02 }, 0.9) })],
  },
  {
    id: "unicode-astral/compare-aspect-200-units-astral",
    class: "unicode-astral",
    note: "100 x U+1F600 is 200 UTF-16 units, exactly the aspect cap.",
    tool: "jev_compare",
    arguments: { passage_a: `${GRIN} yes`, passage_b: `${GRIN} no`, aspects: [GRIN.repeat(100)] },
    responses: [
      ok({ overall: spread("contradicts", COMPARE, 0.9, 0.9), aspect_0: spread("different_facts", COMPARE, 0.9, 0.9) }),
    ],
  },
  {
    id: "unicode-astral/compare-aspect-201-units-astral-rejected",
    class: "unicode-astral",
    note: "Schema rejection: 'a' + 100 x U+1F600 is 201 units, over the 200 cap, though only 101 code points.",
    tool: "jev_compare",
    arguments: { passage_a: "x", passage_b: "y", aspects: [`a${GRIN.repeat(100)}`] },
  },
  {
    id: "unicode-astral/gate-claim-truncation-splits-surrogate",
    class: "unicode-astral",
    divergences: ["ADR-0005"],
    note: "'a' + 1000 x U+1F600 is 2001 units; the 2000-unit cut ends on a high surrogate in the request state.",
    tool: "jev_gate",
    arguments: { ...REVIEW_ARGS, claims: [`a${GRIN.repeat(1000)}`], evidence: "emoji" },
    responses: [ok({ ...STRONG_REVIEW, claim_0: spread("unsupported", CLAIM, 0.9, 0.9) })],
  },
  {
    id: "unicode-astral/classify-astral-ids-preserved",
    class: "unicode-astral",
    tool: "jev_classify",
    arguments: {
      items: [{ id: `${GRIN} launch`, text: "Rocket launch at dawn." }],
      classes: [
        { id: "émoji-class", description: "Space." },
        { id: "другое", description: "Other." },
      ],
    },
    responses: [ok({ i0: spread("c0", ["c0", "c1"], 0.95, 0.95) })],
  },
  {
    id: "unicode-astral/screen-purpose-quoted-in-instruction",
    class: "unicode-astral",
    tool: "jev_screen",
    arguments: { text: `Résumé ${GRIN} "quoted"`, purpose: 'find the "résumé" section' },
    responses: [ok({ injection: noul(0.01), substance: noul(0.9), relevance: noul(0.9) })],
  },

  // ── duplicate-id ──────────────────────────────────────────────────────────
  {
    id: "duplicate-id/classify-duplicate-item-id",
    class: "duplicate-id",
    tool: "jev_classify",
    arguments: {
      items: [
        { id: "t1", text: "a" },
        { id: "t1", text: "b" },
      ],
      classes: [
        { id: "x", description: "x" },
        { id: "y", description: "y" },
      ],
    },
  },
  {
    id: "duplicate-id/classify-duplicate-class-id",
    class: "duplicate-id",
    tool: "jev_classify",
    arguments: {
      items: [{ id: "t1", text: "a" }],
      classes: [
        { id: "x", description: "x" },
        { id: "x", description: "y" },
      ],
    },
  },
  {
    id: "duplicate-id/classify-omitted-ids-get-positional-fallbacks",
    class: "duplicate-id",
    divergences: ["ADR-0031"],
    tool: "jev_classify",
    arguments: {
      items: [{ text: "a" }, { id: "item0", text: "b" }],
      classes: [{ description: "x" }, { id: "class0", description: "y" }],
    },
    responses: [ok({ i0: spread("c1", ["c0", "c1"], 0.9, 0.9), i1: spread("c0", ["c0", "c1"], 0.9, 0.9) })],
  },
  {
    id: "duplicate-id/decide-duplicate-candidate-id",
    class: "duplicate-id",
    tool: "jev_decide",
    arguments: {
      decision: "d",
      evidence: "e",
      priorities: "p",
      candidates: [
        { id: "same", description: "one" },
        { id: "same", description: "two" },
      ],
    },
  },
  {
    id: "duplicate-id/decide-candidate-collides-with-escape-hatch",
    class: "duplicate-id",
    tool: "jev_decide",
    arguments: {
      decision: "d",
      evidence: "e",
      priorities: "p",
      candidates: [
        { id: "investigate", description: "one" },
        { id: "ship", description: "two" },
      ],
    },
  },
  {
    id: "duplicate-id/decide-hatch-name-allowed-without-hatches",
    class: "duplicate-id",
    tool: "jev_decide",
    arguments: {
      decision: "d",
      evidence: "e",
      priorities: "p",
      candidates: [
        { id: "none", description: "Do nothing." },
        { id: "ship", description: "Ship it." },
      ],
      escape_hatches: false,
    },
    responses: [ok({ recommendation: choice("option_0", { option_0: 0.7, option_1: 0.3 }, 0.6) })],
  },
  {
    id: "duplicate-id/rerank-duplicate-id",
    class: "duplicate-id",
    tool: "jev_rerank",
    arguments: {
      query: "q",
      candidates: [
        { id: "dup", text: "a" },
        { id: "dup", text: "b" },
      ],
    },
  },
  {
    id: "duplicate-id/rerank-fallback-avoids-supplied-ids",
    class: "duplicate-id",
    note: "Candidate 1 has no id and candidate1 is taken, so it becomes candidate1_2 (index.ts:711-722).",
    tool: "jev_rerank",
    arguments: {
      query: "q",
      candidates: [{ id: "candidate1", text: "a" }, { text: "b" }, { text: "c" }],
    },
    responses: [ok({ rel_0: noul(0.2), rel_1: noul(0.9), rel_2: noul(0.6) })],
  },
  {
    id: "duplicate-id/extract-duplicate-field-id",
    class: "duplicate-id",
    tool: "jev_extract",
    arguments: {
      document: "a1 b2",
      fields: [
        { id: "n", pattern: "\\d", description: "d" },
        { id: "n", pattern: "[a-z]", description: "l" },
      ],
    },
  },
  {
    id: "duplicate-id/find-ids-sanitized-and-suffixed",
    class: "duplicate-id",
    note: "Ids are sanitized to [A-Za-z0-9_.-], duplicates get _1, an id that sanitizes to empty falls back to candidate{i}.",
    tool: "jev_find",
    arguments: {
      query: "q",
      candidates: [
        { id: "naïve café", text: "one" },
        { id: "naïve café", text: "two" },
        { id: GRIN, text: "three" },
        { id: "__edge__", text: "four" },
      ],
    },
    responses: [
      ok({
        best: choice("na_ve_caf_1", { na_ve_caf: 0.1, na_ve_caf_1: 0.7, candidate2: 0.1, edge: 0.1 }, 0.6),
        exists: noul(0.8),
      }),
    ],
  },
  {
    id: "duplicate-id/verify-duplicate-evidence-ids-suffixed",
    class: "duplicate-id",
    tool: "jev_verify",
    arguments: {
      claims: ["x"],
      evidence: [{ id: "doc", text: "one" }, { id: "doc", text: "two" }, { text: "three" }],
    },
    responses: [
      ok({
        relation_claim0: spread("supports", RELATION, 0.9, 0.9),
        source_claim0: spread("doc_1", ["doc", "doc_1", "evidence2", "none"], 0.85, 0.8),
      }),
    ],
  },
];
