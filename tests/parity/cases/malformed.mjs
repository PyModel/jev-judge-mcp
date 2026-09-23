// Answers and envelopes that must fail closed, for the tools and branches the
// reference mock suite leaves thin (compare, rerank, decide, extract, classify).
import { choice, CHECK, CLAIM, COMPARE, noul, ok, REVIEW_ARGS, spread, STRONG_REVIEW, VERIFY_ARGS } from "./_helpers.mjs";

const DECIDE_ARGS = {
  decision: "Which cache should the API use?",
  evidence: "Redis is deployed. Memcached is not.",
  priorities: "Reuse existing infrastructure.",
  candidates: [
    { id: "redis", description: "Redis." },
    { id: "memcached", description: "Memcached." },
  ],
  requirements: ["Reuses existing infrastructure.", "Supports TTL per key."],
};
const REC_KEYS = ["option_0", "option_1", "ask_user", "investigate", "none"];

export default [
  {
    id: "malformed/compare-invalid-overall-and-aspect",
    class: "malformed",
    note: "overall sums to 1.05 and aspect_1 picks a non-argmax key: both invalid_response, with status last in the shape (index.ts:847-854).",
    tool: "jev_compare",
    arguments: { passage_a: "Ships Monday at $10.", passage_b: "Ships Tuesday at $10.", aspects: ["price", "ship date"] },
    responses: [
      ok({
        overall: choice("contradicts", { same_fact: 0.1, contradicts: 0.9, different_facts: 0.05 }, 0.9),
        aspect_0: spread("same_fact", COMPARE, 0.9, 0.9),
        aspect_1: choice("same_fact", { same_fact: 0.2, contradicts: 0.7, different_facts: 0.1 }, 0.7),
      }),
    ],
  },
  {
    id: "malformed/rerank-missing-noul",
    class: "malformed",
    tool: "jev_rerank",
    arguments: { query: "q", candidates: [{ text: "one" }, { text: "two" }, { text: "three" }] },
    responses: [ok({ rel_0: noul(0.4), rel_2: noul(0.9) })],
  },
  {
    id: "malformed/rerank-out-of-range-noul",
    class: "malformed",
    tool: "jev_rerank",
    arguments: { query: "q", candidates: [{ id: "x", text: "one" }, { id: "y", text: "two" }] },
    responses: [ok({ rel_0: noul(0.4), rel_1: noul(1.2) })],
  },
  {
    id: "malformed/decide-invalid-recommendation",
    class: "malformed",
    note: "Missing escape-hatch keys: recommendation becomes the invalid_response shape (index.ts:665).",
    tool: "jev_decide",
    arguments: DECIDE_ARGS,
    responses: [
      ok({
        recommendation: choice("option_0", { option_0: 0.9, option_1: 0.1 }, 0.9),
        check_0_0: spread("supported", CHECK),
        check_0_1: spread("supported", CHECK),
        check_1_0: spread("contradicted", CHECK),
        check_1_1: spread("supported", CHECK),
      }),
    ],
  },
  {
    id: "malformed/decide-invalid-check-kept-silent",
    class: "malformed",
    note: "Quirk Q3: the malformed check_0_0 stays in checks as invalid_response and never reaches warnings; check_0_1 is contradicted.",
    tool: "jev_decide",
    arguments: DECIDE_ARGS,
    responses: [
      ok({
        recommendation: spread("option_0", REC_KEYS, 0.8, 0.8),
        check_0_0: choice("contradicted", { supported: 0.7, contradicted: 0.2, unknown: 0.1 }, 0.7),
        check_0_1: spread("contradicted", CHECK),
        check_1_0: spread("supported", CHECK),
      }),
    ],
  },
  {
    id: "malformed/extract-sum-0-99-rejected",
    class: "malformed",
    note: "Quirk Q2: extract uses <= 0.01, so a 0.99 sum (0.010000000000000009 from 1) is invalid here and valid in the shared validator.",
    tool: "jev_extract",
    arguments: {
      document: "Order 1001 shipped; order 1002 is pending.",
      fields: [{ id: "shipped", pattern: "\\d{4}", description: "The shipped order number." }],
    },
    responses: [ok({ f0: choice("c0", { c0: 0.33, c1: 0.33, none_of_them: 0.33 }, 0.5) })],
  },
  {
    id: "malformed/extract-choice-not-argmax",
    class: "malformed",
    tool: "jev_extract",
    arguments: {
      document: "Order 1001 shipped; order 1002 is pending.",
      fields: [{ id: "shipped", pattern: "\\d{4}", description: "The shipped order number." }],
    },
    responses: [ok({ f0: choice("c1", { c0: 0.8, c1: 0.15, none_of_them: 0.05 }, 0.8) })],
  },
  {
    id: "malformed/classify-extra-probability-key",
    class: "malformed",
    tool: "jev_classify",
    arguments: {
      items: [
        { id: "a", text: "Refund please." },
        { id: "b", text: "The app crashes." },
      ],
      classes: [
        { id: "billing", description: "Payments." },
        { id: "technical", description: "Bugs." },
      ],
    },
    responses: [
      ok({
        i0: choice("c0", { c0: 0.9, c1: 0.05, c2: 0.05 }, 0.9),
        i1: spread("c1", ["c0", "c1"], 0.95, 0.95),
      }),
    ],
  },
  {
    id: "malformed/gate-rubric-confidence-out-of-range",
    class: "malformed",
    note: "correctness confidence 1.5 becomes null; unknown rubric confidence escalates the review half.",
    tool: "jev_gate",
    arguments: { ...REVIEW_ARGS, claims: ["It works."], evidence: "It works." },
    responses: [
      ok({
        ...STRONG_REVIEW,
        correctness: { score: 2, confidence: 1.5 },
        claim_0: spread("verified", CLAIM, 0.95, 0.95),
      }),
    ],
  },
  {
    id: "malformed/verify-claim-choice-inherited-key",
    class: "malformed",
    tool: "jev_verify",
    arguments: VERIFY_ARGS,
    responses: [ok({ relation_claim0: { choice: "hasOwnProperty", confidence: 0.9, probabilities: { hasOwnProperty: 1 } } })],
  },
  {
    id: "malformed/envelope-body-not-json",
    class: "malformed",
    tool: "jev_verify",
    arguments: VERIFY_ARGS,
    responses: [{ status: 200, body: "<html>gateway</html>" }],
  },
  {
    id: "malformed/envelope-body-array",
    class: "malformed",
    tool: "jev_screen",
    arguments: { text: "hello" },
    responses: [{ status: 200, body: "[]" }],
  },
  {
    id: "malformed/envelope-usage-null-model-absent",
    class: "malformed",
    note: "usage null is tolerated and reported as zeros; no model key keeps the requested model.",
    tool: "jev_screen",
    arguments: { text: "hello" },
    responses: [{ status: 200, body: { answers: { injection: noul(0.01), substance: noul(0.9) }, usage: null } }],
  },
  {
    id: "malformed/envelope-usage-infinite",
    class: "malformed",
    note: "1e999 parses to Infinity, which fails the finite token check.",
    tool: "jev_screen",
    arguments: { text: "hello" },
    responses: [
      {
        status: 200,
        body: '{"answers":{"injection":{"noul":0.01},"substance":{"noul":0.9}},"usage":{"input_tokens":1e999,"output_tokens":1}}',
      },
    ],
  },
];
