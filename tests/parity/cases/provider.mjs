// Provider failures (non-2xx from the compatible endpoint) and resolver
// errors (provider resolution throws before any HTTP; no fake server).
import { ok, VERIFY_ARGS } from "./_helpers.mjs";

const NEVER_CONTACTED = "https://jev-compatible.invalid/v1/systemone";

const resolver = (id, env, extra = {}) => ({
  id: `resolver-error/${id}`,
  class: "resolver-error",
  tool: "jev_verify",
  arguments: VERIFY_ARGS,
  env,
  ...extra,
});

export default [
  // ── provider-failure ──────────────────────────────────────────────────────
  {
    id: "provider-failure/compatible-500-text",
    class: "provider-failure",
    tool: "jev_verify",
    arguments: VERIFY_ARGS,
    responses: [{ status: 500, body: "upstream exploded" }],
  },
  {
    id: "provider-failure/compatible-503-body-sliced-at-200",
    class: "provider-failure",
    tool: "jev_screen",
    arguments: { text: "hello" },
    responses: [{ status: 503, body: `${"0123456789".repeat(25)}END` }],
  },
  {
    id: "provider-failure/compatible-429-echoes-key-redacted",
    class: "provider-failure",
    note: "The echoed JEV_API_KEY is replaced with [redacted] before slicing (provider.ts:172).",
    tool: "jev_classify",
    arguments: {
      items: [{ id: "a", text: "x" }],
      classes: [
        { id: "p", description: "p" },
        { id: "q", description: "q" },
      ],
    },
    responses: [{ status: 429, body: '{"error":"rate limited for Bearer parity-test-key","key":"parity-test-key"}' }],
  },
  {
    id: "provider-failure/compatible-502-empty-body",
    class: "provider-failure",
    tool: "jev_gate",
    arguments: { request: "r", diff: "d", claims: ["c"], evidence: "e" },
    responses: [{ status: 502, body: "" }],
  },
  {
    id: "provider-failure/compatible-401-after-regex",
    class: "provider-failure",
    note: "extract runs its regex fields, then the request fails: the whole call is a tool error.",
    tool: "jev_extract",
    arguments: { document: "id 42", fields: [{ id: "n", pattern: "\\d+", description: "The id." }] },
    responses: [{ status: 401, body: '{"error":"unauthorized"}' }],
  },
  {
    id: "provider-failure/compatible-200-control",
    class: "provider-failure",
    note: "Control: the same extract call with a 200 envelope.",
    tool: "jev_extract",
    arguments: { document: "id 42", fields: [{ id: "n", pattern: "\\d+", description: "The id." }] },
    responses: [ok({ f0: { choice: "c0", confidence: 0.99, probabilities: { c0: 0.99, none_of_them: 0.01 } } })],
  },

  // ── resolver-error ────────────────────────────────────────────────────────
  resolver("auto-no-credentials", {}),
  resolver("auto-openrouter-key-not-sk-or", { OPENROUTER_API_KEY: "or-v1-not-an-sk-or-key" }),
  resolver("auto-unknown-provider-name", { JEV_PROVIDER: "bogus" }, { note: "An unknown JEV_PROVIDER falls through to auto resolution." }),
  resolver("auto-compatible-key-without-url", { JEV_API_KEY: "parity-test-key" }),
  resolver("auto-cloudflare-token-without-account", { CLOUDFLARE_API_TOKEN: "cf-token" }),
  resolver("explicit-typesafe-unset", { JEV_PROVIDER: "typesafe" }),
  resolver("explicit-typesafe-uppercase-unset", { JEV_PROVIDER: "TypeSafe" }, { note: "JEV_PROVIDER is lowercased before matching." }),
  resolver("explicit-openrouter-unset", { JEV_PROVIDER: "openrouter" }),
  resolver("explicit-openrouter-not-sk-or", { JEV_PROVIDER: "openrouter", OPENROUTER_API_KEY: "sk-ant-not-openrouter" }),
  resolver("explicit-cloudflare-unset", { JEV_PROVIDER: "cloudflare" }),
  resolver("explicit-cloudflare-account-without-token", { JEV_PROVIDER: "cloudflare", CLOUDFLARE_ACCOUNT_ID: "acct" }),
  resolver("explicit-vercel-unset", { JEV_PROVIDER: "vercel" }, {
    divergences: ["ADR-0007"],
    note: "Python keeps the slot but reports vercel as unsupported (ADR-0007).",
  }),
  resolver("explicit-compatible-both-unset", { JEV_PROVIDER: "compatible" }),
  resolver("explicit-compatible-key-unset", { JEV_PROVIDER: "compatible", JEV_API_BASE_URL: NEVER_CONTACTED }),
  resolver("explicit-compatible-url-unset", { JEV_PROVIDER: "compatible", JEV_API_KEY: "parity-test-key" }),
  resolver("explicit-compatible-other-tool", { JEV_PROVIDER: "compatible" }, {
    tool: "jev_review",
    arguments: { request: "r", diff: "d" },
  }),
  resolver("extract-zero-match-never-resolves", {}, {
    tool: "jev_extract",
    arguments: { document: "no digits here", fields: [{ id: "n", pattern: "\\d+", description: "A number." }] },
    note: "No field has candidates, so askJev never runs and missing credentials are not an error.",
  }),
];
