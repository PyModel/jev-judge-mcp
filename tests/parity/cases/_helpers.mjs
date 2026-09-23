// Builders for fixture-class cases. A case is
//   { id, class, tool, arguments, responses?, env?, divergences?, note? }
// `responses` are replayed in order by the fake provider; `body` is a JSON
// value or, for malformed envelopes, the exact response text.

export const USAGE = { input_tokens: 321, output_tokens: 17 };

export const ok = (answers, extra = {}) => ({ status: 200, body: { answers, usage: USAGE, ...extra } });

export const choice = (pick, probabilities, confidence = 0.95) => ({ choice: pick, confidence, probabilities });

// `top` on `pick`, the rest split evenly (rounded so the wire stays readable).
export function spread(pick, keys, top = 0.9, confidence = 0.95) {
  const rest = keys.filter((k) => k !== pick);
  const each = rest.length > 0 ? Number(((1 - top) / rest.length).toFixed(6)) : 0;
  const probabilities = {};
  for (const k of keys) probabilities[k] = k === pick ? top : each;
  return choice(pick, probabilities, confidence);
}

export const noul = (p) => ({ noul: p });
export const score = (s, confidence = 0.9) => ({ score: s, confidence });

export const RELATION = ["supports", "contradicts", "says_nothing"];
export const CLAIM = ["verified", "contradicted", "unsupported"];
export const COMPARE = ["same_fact", "contradicts", "different_facts"];
export const CHECK = ["supported", "contradicted", "unknown"];
export const HATCHES = ["ask_user", "investigate", "none"];

export const REVIEW_ARGS = {
  request: "Return 404 instead of 500 when a user id is unknown.",
  diff: "--- a/api/users.ts\n+++ b/api/users.ts\n@@\n-  if (!user) throw new Error('missing');\n+  if (!user) return res.status(404).json({ error: 'not found' });",
  tests: "PASS api/users.test.ts\n  ✓ returns 404 for an unknown id (12 ms)",
};

export const STRONG_REVIEW = {
  correctness: score(2, 0.95),
  spec_match: score(2, 0.95),
  test_gap: score(0, 0.9),
  blast_radius: score(0, 0.9),
  safe_to_apply: noul(0.95),
};

export const GATE_ARGS = {
  ...REVIEW_ARGS,
  claims: ["Unknown user ids now return 404.", "A regression test covers the 404 path."],
  evidence: [
    { id: "diff", text: "+  if (!user) return res.status(404).json({ error: 'not found' });" },
    { id: "test-log", text: "PASS api/users.test.ts\n  ✓ returns 404 for an unknown id (12 ms)" },
  ],
};

export const VERIFY_ARGS = { claims: ["The service listens on port 8080."], evidence: "server.listen(8080)" };
