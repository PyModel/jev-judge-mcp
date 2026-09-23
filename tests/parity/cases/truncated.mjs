// Inputs cut at a cap before reaching Jev: the marker text, where it shows up,
// and the rule that truncated context is never auto.
import { CLAIM, noul, ok, REVIEW_ARGS, spread, STRONG_REVIEW } from "./_helpers.mjs";

export default [
  {
    id: "truncated/find-candidate-text-over-cap",
    class: "truncated",
    note: "2000 units pass untouched; 2001 are cut to 2000 plus ' […truncated]', and the cut text is what top[] returns.",
    tool: "jev_find",
    arguments: {
      query: "which note is longest",
      candidates: [
        { id: "exact", text: "e".repeat(2000) },
        { id: "over", text: "o".repeat(2001) },
      ],
    },
    responses: [ok({ best: spread("over", ["exact", "over"], 0.6, 0.5), exists: noul(0.8) })],
  },
  {
    id: "truncated/classify-item-and-class-text-over-cap",
    class: "truncated",
    tool: "jev_classify",
    arguments: {
      items: [{ id: "long", text: "i".repeat(2500) }],
      classes: [
        { id: "a", description: "d".repeat(2100) },
        { id: "b", description: "Short class." },
      ],
    },
    responses: [ok({ i0: spread("c0", ["c0", "c1"], 0.95, 0.95) })],
  },
  {
    id: "truncated/gate-claim-over-cap",
    class: "truncated",
    note: "The state carries the cut claim; results[].claim echoes the caller's full text. incomplete_context demotes auto.",
    tool: "jev_gate",
    arguments: { ...REVIEW_ARGS, claims: [`The fix is complete. ${"x".repeat(2000)}`], evidence: "The fix is complete." },
    responses: [ok({ ...STRONG_REVIEW, claim_0: spread("verified", CLAIM, 0.95, 0.95) })],
  },
  {
    id: "truncated/gate-evidence-item-over-cap",
    class: "truncated",
    tool: "jev_gate",
    arguments: {
      ...REVIEW_ARGS,
      claims: ["The log shows the test passing."],
      evidence: [
        { id: "log", text: `PASS returns 404\n${"l".repeat(50000)}` },
        { id: "note", text: "short" },
      ],
    },
    responses: [ok({ ...STRONG_REVIEW, claim_0: spread("verified", CLAIM, 0.95, 0.95) })],
  },
  {
    id: "truncated/review-tests-over-cap",
    class: "truncated",
    tool: "jev_review",
    arguments: { ...REVIEW_ARGS, tests: `PASS\n${"t".repeat(50000)}` },
    responses: [ok(STRONG_REVIEW)],
  },
  {
    id: "truncated/review-request-exactly-at-cap",
    class: "truncated",
    note: "50000 units is not over the cap: truncated stays false and auto stands.",
    tool: "jev_review",
    arguments: { ...REVIEW_ARGS, request: "r".repeat(50000) },
    responses: [ok(STRONG_REVIEW)],
  },
];
