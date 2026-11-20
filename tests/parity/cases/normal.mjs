// One well-formed call per tool on its main success path.
import { choice, CHECK, CLAIM, COMPARE, GATE_ARGS, noul, ok, RELATION, REVIEW_ARGS, spread, STRONG_REVIEW } from "./_helpers.mjs";

export default [
  {
    id: "normal/verify-multi-evidence-model-override",
    class: "normal",
    note: "body.model is a string, so it replaces the requested model in the output (provider.ts:199).",
    tool: "jev_verify",
    arguments: {
      claims: ["The build passes on main.", "Line coverage is 91%."],
      evidence: [
        { id: "ci-log", text: "main #812: all 214 tests passed in 3m12s" },
        { id: "coverage", text: "Statements: 91.4% | Lines: 91.0% | Branches: 84.2%" },
      ],
    },
    responses: [
      ok(
        {
          relation_claim0: spread("supports", RELATION, 0.93, 0.91),
          source_claim0: spread("ci-log", ["ci-log", "coverage", "none"], 0.88, 0.86),
          relation_claim1: spread("supports", RELATION, 0.9, 0.87),
          source_claim1: spread("coverage", ["ci-log", "coverage", "none"], 0.95, 0.94),
        },
        { model: "jev-1.13" },
      ),
    ],
  },
  {
    id: "normal/screen-with-purpose-pass",
    class: "normal",
    tool: "jev_screen",
    arguments: {
      text: "Install with `pip install httpx`. Timeouts default to 5 seconds and can be set per request.",
      purpose: "configure HTTP client timeouts",
    },
    responses: [ok({ injection: noul(0.02), substance: noul(0.96), relevance: noul(0.91) })],
  },
  {
    id: "normal/find-ranked-top-k",
    class: "normal",
    note: "Caller ids are sanitized for the wire; the third candidate has no id and gets candidate2.",
    tool: "jev_find",
    arguments: {
      query: "How do I rotate the API key?",
      candidates: [
        { id: "docs/install.md", text: "Install the CLI with brew install acme." },
        { id: "docs/keys.md", text: "Rotate a key with `acme keys rotate`; the old key stays valid for 24 hours." },
        { text: "Billing questions go to billing@acme.test." },
      ],
      top_k: 2,
    },
    responses: [
      ok({
        best: choice("docs_keys.md", { "docs_install.md": 0.07, "docs_keys.md": 0.9, candidate2: 0.03 }, 0.9),
        exists: noul(0.94),
      }),
    ],
  },
  {
    id: "normal/classify-batch-with-context",
    class: "normal",
    tool: "jev_classify",
    arguments: {
      items: [
        { id: "t-101", text: "I was charged twice for March." },
        { id: "t-102", text: "Do you offer a nonprofit discount?" },
        { text: "The export button does nothing in Firefox." },
      ],
      classes: [
        { id: "billing", description: "Charges, refunds, invoices." },
        { id: "sales", description: "Pricing, discounts, plans." },
        { id: "technical", description: "Bugs and product behavior." },
        { id: "manual_review", description: "Anything that fits no class or needs a human." },
      ],
      purpose: "Route support tickets.",
      context: { plan_names: ["free", "team", "enterprise"] },
    },
    responses: [
      ok({
        i0: spread("c0", ["c0", "c1", "c2", "c3"], 0.94, 0.93),
        i1: choice("c1", { c0: 0.2, c1: 0.6, c2: 0.05, c3: 0.15 }, 0.58),
        i2: spread("c2", ["c0", "c1", "c2", "c3"], 0.91, 0.9),
      }),
    ],
  },
  {
    id: "normal/decide-with-requirements",
    class: "normal",
    tool: "jev_decide",
    arguments: {
      decision: "Which queue backend should the job runner use?",
      evidence: "Redis is already deployed. Postgres LISTEN/NOTIFY drops messages when no listener is connected. SQS adds a new AWS dependency.",
      priorities: "No new infrastructure. At-least-once delivery.",
      candidates: [
        { id: "redis-streams", description: "Redis Streams with consumer groups." },
        { id: "pg-notify", description: "Postgres LISTEN/NOTIFY." },
        { id: "sqs", description: "Amazon SQS standard queue." },
      ],
      requirements: ["Needs no new infrastructure.", "Delivers at least once."],
    },
    responses: [
      ok({
        recommendation: choice(
          "option_0",
          { option_0: 0.82, option_1: 0.04, option_2: 0.06, ask_user: 0.03, investigate: 0.04, none: 0.01 },
          0.8,
        ),
        check_0_0: spread("supported", CHECK),
        check_0_1: spread("supported", CHECK),
        check_1_0: spread("supported", CHECK),
        check_1_1: spread("contradicted", CHECK),
        check_2_0: spread("contradicted", CHECK),
        check_2_1: spread("supported", CHECK),
      }),
    ],
  },
  {
    id: "normal/rerank-top-k",
    class: "normal",
    tool: "jev_rerank",
    arguments: {
      query: "retry policy for failed webhooks",
      candidates: [
        { id: "a", text: "Webhook payload schema reference." },
        { id: "b", text: "Failed webhooks retry with exponential backoff for 72 hours." },
        { id: "c", text: "Webhook signing secrets rotate monthly." },
        { id: "d", text: "Delivery attempts are logged with their HTTP status." },
      ],
      top_k: 3,
    },
    responses: [ok({ rel_0: noul(0.2), rel_1: noul(0.93), rel_2: noul(0.123456), rel_3: noul(0.71) })],
  },
  {
    id: "normal/compare-with-aspects",
    class: "normal",
    tool: "jev_compare",
    arguments: {
      passage_a: "Acme Pro costs $20 per seat per month and launched in March 2026.",
      passage_b: "Acme Pro launched in March 2026 at $25 per seat per month.",
      aspects: ["launch date", "price"],
      purpose: "reconcile the pricing page with the press release",
    },
    responses: [
      ok({
        overall: spread("contradicts", COMPARE, 0.9, 0.88),
        aspect_0: spread("same_fact", COMPARE, 0.95, 0.94),
        aspect_1: spread("contradicts", COMPARE, 0.92, 0.9),
      }),
    ],
  },
  {
    id: "normal/extract-two-fields",
    class: "normal",
    tool: "jev_extract",
    arguments: {
      document: "Invoice INV-2041 issued 2026-03-14. Total due: $1,249.00 (was $1,399.00). Pay by 2026-04-13.",
      fields: [
        { id: "total", pattern: "\\$[0-9,]+\\.[0-9]{2}", description: "The amount currently due." },
        { id: "issued", pattern: "\\d{4}-\\d{2}-\\d{2}", description: "The invoice issue date." },
      ],
      purpose: "bookkeeping import",
    },
    responses: [
      ok({
        f0: choice("c0", { c0: 0.92, c1: 0.06, none_of_them: 0.02 }, 0.9),
        f1: choice("c0", { c0: 0.96, c1: 0.03, none_of_them: 0.01 }, 0.95),
      }),
    ],
  },
  {
    id: "normal/review-auto-env-model",
    class: "normal",
    note: "JEV_MCP_MODEL reaches the wire and the output; body.model is null, so it is not overridden.",
    env: { JEV_MCP_MODEL: "jev-1.12" },
    tool: "jev_review",
    arguments: REVIEW_ARGS,
    responses: [ok(STRONG_REVIEW, { model: null })],
  },
  {
    id: "normal/gate-accepted",
    class: "normal",
    tool: "jev_gate",
    arguments: GATE_ARGS,
    responses: [
      ok({
        ...STRONG_REVIEW,
        claim_0: spread("verified", CLAIM, 0.95, 0.95),
        claim_1: spread("verified", CLAIM, 0.93, 0.92),
      }),
    ],
  },
];
