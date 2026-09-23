// jev_extract's regex side: zero matches, the 1000 ms worker deadline, and
// patterns V8 rejects or the Python subset (ADR-0004) will reject.
import { choice, ok } from "./_helpers.mjs";

const DOC = "Build ABC-123 passed. Build ABC-124 failed. Contact: ops@example.test";
const CODE_FIELD = { id: "build", pattern: "[A-Z]{3}-\\d+", description: "The build id that failed." };
// Nested quantifiers over a run of 'a' ended by '!' backtrack exponentially in V8.
const CATASTROPHIC = { id: "slow", pattern: "(a+)+$", description: "A pathological pattern." };
const SLOW_DOC = `${"a".repeat(40)}! Build ABC-123 passed. Build ABC-124 failed.`;

export default [
  // ── zero-match ────────────────────────────────────────────────────────────
  {
    id: "zero-match/extract-no-matches-no-provider-call",
    class: "zero-match",
    note: "No field has candidates: no request, provider 'none', usage null, model is the configured model.",
    tool: "jev_extract",
    arguments: { document: DOC, fields: [{ id: "phone", pattern: "\\+\\d{11}", description: "A phone number." }] },
    responses: [],
  },
  {
    id: "zero-match/extract-one-field-matches",
    class: "zero-match",
    note: "Only f1 reaches the wire; the zero-match field is not_found/no_regex_matches.",
    tool: "jev_extract",
    arguments: {
      document: DOC,
      fields: [{ id: "phone", pattern: "\\+\\d{11}", description: "A phone number." }, CODE_FIELD],
    },
    responses: [ok({ f1: choice("c1", { c0: 0.03, c1: 0.95, none_of_them: 0.02 }, 0.94) })],
  },
  {
    id: "zero-match/extract-zero-length-matches-dropped",
    class: "zero-match",
    note: "A pattern that only matches the empty string yields no candidates.",
    tool: "jev_extract",
    arguments: { document: DOC, fields: [{ id: "empty", pattern: "q*", description: "Nothing." }] },
    responses: [],
  },

  // ── regex-timeout ─────────────────────────────────────────────────────────
  {
    id: "regex-timeout/extract-catastrophic-then-valid-field",
    class: "regex-timeout",
    note: "The slow field is invalid_pattern with the verbatim timeout text; the next field still runs and is judged.",
    tool: "jev_extract",
    arguments: { document: SLOW_DOC, fields: [CATASTROPHIC, CODE_FIELD] },
    responses: [ok({ f1: choice("c1", { c0: 0.04, c1: 0.94, none_of_them: 0.02 }, 0.93) })],
  },
  {
    id: "regex-timeout/extract-only-field-times-out",
    class: "regex-timeout",
    tool: "jev_extract",
    arguments: { document: SLOW_DOC, fields: [CATASTROPHIC] },
    responses: [],
  },

  // ── invalid-pattern ───────────────────────────────────────────────────────
  {
    id: "invalid-pattern/extract-syntax-error",
    class: "invalid-pattern",
    divergences: ["ADR-0004"],
    note: "The reason is V8's SyntaxError message; Python returns a named reason (ADR-0004).",
    tool: "jev_extract",
    arguments: { document: DOC, fields: [{ id: "bad", pattern: "(?/", description: "Broken." }, CODE_FIELD] },
    responses: [ok({ f1: choice("c0", { c0: 0.9, c1: 0.08, none_of_them: 0.02 }, 0.9) })],
  },
  {
    id: "invalid-pattern/extract-unknown-flag",
    class: "invalid-pattern",
    divergences: ["ADR-0004"],
    tool: "jev_extract",
    arguments: { document: DOC, fields: [{ ...CODE_FIELD, flags: "x" }] },
    responses: [],
  },
  {
    id: "invalid-pattern/extract-unicode-flag",
    class: "invalid-pattern",
    divergences: ["ADR-0004"],
    note: "V8 accepts the u flag; the Python subset rejects it as invalid_pattern.",
    tool: "jev_extract",
    arguments: { document: DOC, fields: [{ ...CODE_FIELD, flags: "u" }] },
    responses: [ok({ f0: choice("c1", { c0: 0.05, c1: 0.9, none_of_them: 0.05 }, 0.9) })],
  },
  {
    id: "invalid-pattern/extract-multiline-flag",
    class: "invalid-pattern",
    divergences: ["ADR-0004"],
    note: "V8 accepts m; the Python subset rejects it.",
    tool: "jev_extract",
    arguments: { document: "id=7\nid=8", fields: [{ id: "last", pattern: "\\d$", flags: "m", description: "The last id." }] },
    responses: [ok({ f0: choice("c1", { c0: 0.05, c1: 0.9, none_of_them: 0.05 }, 0.9) })],
  },
  {
    id: "invalid-pattern/extract-variable-lookbehind",
    class: "invalid-pattern",
    divergences: ["ADR-0004"],
    note: "V8 accepts variable-length lookbehind; the Python subset rejects it.",
    tool: "jev_extract",
    arguments: { document: DOC, fields: [{ id: "num", pattern: "(?<=ABC-\\d*)\\d{3}", description: "Build number." }] },
    responses: [ok({ f0: choice("c0", { c0: 0.5, c1: 0.45, none_of_them: 0.05 }, 0.4) })],
  },
  {
    id: "invalid-pattern/extract-named-group",
    class: "invalid-pattern",
    divergences: ["ADR-0004"],
    note: "V8 accepts named groups; the Python subset rejects them.",
    tool: "jev_extract",
    arguments: { document: DOC, fields: [{ id: "num", pattern: "ABC-(?<n>\\d+)", description: "Build id." }] },
    responses: [ok({ f0: choice("c0", { c0: 0.5, c1: 0.45, none_of_them: 0.05 }, 0.4) })],
  },
];
