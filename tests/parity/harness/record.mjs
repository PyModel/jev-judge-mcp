// Regenerate tests/parity/fixtures/ from the pinned reference (make parity-record).
//
//   fixtures/mock/     one file per runtime case of the reference mock suite
//   fixtures/classes/  one file per ROADMAP P0 fixture-class case (cases/*.mjs)
//   fixtures/index.json
//
// Output is a pure function of the pinned reference, Node version, and the
// case files: no ports, timestamps, durations, or absolute paths.
import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  callTool,
  connect,
  FIXTURES_DIR,
  PARITY_DIR,
  recordEnv,
  REFERENCE_DIR,
  serialize,
  startFake,
  writeFixture,
} from "./lib.mjs";

const HARNESS_DIR = path.join(PARITY_DIR, "harness");
const CASES_DIR = path.join(PARITY_DIR, "cases");
const CLASSES = [
  "normal",
  "boundary",
  "malformed",
  "truncated",
  "low-confidence",
  "tie",
  "contradicted",
  "unsupported",
  "zero-match",
  "regex-timeout",
  "invalid-pattern",
  "provider-failure",
  "resolver-error",
  "unicode-astral",
  "duplicate-id",
];

rmSync(FIXTURES_DIR, { recursive: true, force: true });
recordMockSuite();
await recordClasses();
writeIndex();

// Swap the reference suite's test/assert/withMock for the recording hooks and
// run it. Everything else in the file (cases, answers, helpers) is untouched.
function recordMockSuite() {
  const source = readFileSync(path.join(REFERENCE_DIR, "test/mock.test.mjs"), "utf8");
  const hooks = pathToFileURL(path.join(HARNESS_DIR, "mock-hooks.mjs")).href;
  const start = source.indexOf("async function withMock(");
  const end = source.indexOf("\n}\n", start) + 3;
  let instrumented =
    source.slice(0, start) + `import { withMock } from ${JSON.stringify(hooks)};\n` + source.slice(end);
  instrumented = replaceOnce(instrumented, 'import assert from "node:assert/strict";', `import { assert } from ${JSON.stringify(hooks)};`);
  instrumented = replaceOnce(instrumented, 'import { test } from "node:test";', `import { test } from ${JSON.stringify(hooks)};`);

  const file = path.join(REFERENCE_DIR, "test", ".parity-mock.test.mjs");
  writeFileSync(file, instrumented);
  try {
    const run = spawnSync(process.execPath, ["--test", "--test-concurrency=1", "--test-reporter=dot", file], {
      encoding: "utf8",
      env: { PATH: process.env.PATH, HOME: process.env.HOME, NO_COLOR: "1" },
    });
    if (run.status !== 0) {
      throw new Error(`instrumented mock suite failed (exit ${run.status}):\n${run.stdout}\n${run.stderr}`);
    }
  } finally {
    rmSync(file, { force: true });
  }
}

function replaceOnce(text, from, to) {
  const at = text.indexOf(from);
  if (at < 0 || text.indexOf(from, at + 1) >= 0) throw new Error(`expected exactly one ${from}`);
  return text.slice(0, at) + to + text.slice(at + from.length);
}

async function recordClasses() {
  const seen = new Set();
  for (const file of readdirSync(CASES_DIR).filter((f) => f.endsWith(".mjs") && !f.startsWith("_")).sort()) {
    const { default: cases } = await import(pathToFileURL(path.join(CASES_DIR, file)).href);
    for (const c of cases) {
      if (!CLASSES.includes(c.class)) throw new Error(`${c.id}: unknown class ${c.class}`);
      if (seen.has(c.id)) throw new Error(`duplicate case id ${c.id}`);
      seen.add(c.id);
      await recordCase(c);
    }
  }
}

async function recordCase(c) {
  const responses = (c.responses ?? []).map((r) => ({
    status: r.status ?? 200,
    body: typeof r.body === "string" ? r.body : JSON.stringify(r.body),
  }));
  let fake = null;
  let env;
  if (c.class === "resolver-error") {
    // Resolution throws before any HTTP: no fake server, no network.
    env = c.env;
  } else {
    let next = 0;
    fake = await startFake(() => {
      if (next >= responses.length) throw new Error("more requests than recorded responses");
      return responses[next++];
    });
    env = {
      JEV_PROVIDER: "compatible",
      JEV_API_KEY: "parity-test-key",
      JEV_API_BASE_URL: `${fake.url}/v1/systemone`,
      ...c.env,
    };
  }
  const client = await connect(env);
  let result;
  try {
    result = await callTool(client, c.tool, c.arguments);
  } finally {
    await client.close();
    await fake?.close();
  }
  const exchanges = fake?.exchanges ?? [];
  for (const e of exchanges) if (e.harness_error) throw new Error(`${c.id}: ${e.harness_error}`);
  if (exchanges.length !== responses.length) {
    throw new Error(`${c.id}: reference made ${exchanges.length} requests, case supplies ${responses.length} responses`);
  }
  writeFixture(
    `classes/${c.id}.json`,
    {
      id: c.id,
      source: { case: c.id },
      classes: [c.class],
      divergences: c.divergences ?? [],
      ...(c.note ? { note: c.note } : {}),
      calls: [
        {
          env: recordEnv(env, fake?.url),
          tool: c.tool,
          arguments: c.arguments,
          exchanges,
          result,
        },
      ],
    },
    fake ? [fake.port] : [],
  );
}

function writeIndex() {
  const entries = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
      const full = path.join(dir, name.name);
      if (name.isDirectory()) walk(full);
      else if (name.name.endsWith(".json")) {
        const fixture = JSON.parse(readFileSync(full, "utf8"));
        entries.push({
          file: path.relative(FIXTURES_DIR, full),
          id: fixture.id,
          classes: fixture.classes,
          divergences: fixture.divergences,
          tools: [...new Set(fixture.calls.map((call) => call.tool))],
          calls: fixture.calls.length,
          provider_requests: fixture.calls.reduce((n, call) => n + call.exchanges.length, 0),
        });
      }
    }
  };
  walk(FIXTURES_DIR);
  const byClass = Object.fromEntries(["mock", ...CLASSES].map((k) => [k, entries.filter((e) => e.classes.includes(k)).length]));
  const missing = CLASSES.filter((k) => byClass[k] === 0);
  if (missing.length > 0) throw new Error(`no fixtures for classes: ${missing.join(", ")}`);
  writeFileSync(
    path.join(FIXTURES_DIR, "index.json"),
    serialize({
      reference: { version: "0.5.0", commit: "69ffb4b49c88802ec6e49b883f4a36b91d23197e", node: process.version },
      counts: { fixtures: entries.length, calls: entries.reduce((n, e) => n + e.calls, 0), by_class: byClass },
      fixtures: entries,
    }),
  );
}
