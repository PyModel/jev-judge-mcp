// Replacements for `test`, `assert`, and `withMock` in an instrumented copy of
// the reference's test/mock.test.mjs (see record.mjs). Every test case keeps
// its own answers, arguments, and response overrides; the provider is pinned
// to `compatible` against the harness fake, and every tool call is recorded.
import strictAssert from "node:assert/strict";
import { test as nodeTest } from "node:test";
import { callTool, connect, recordEnv, slug, startFake, writeFixture } from "./lib.mjs";

const MOCK_FILE = "test/mock.test.mjs";
let registered = 0;
let current = null;

const firstLine = (error) => String(error?.message ?? error).split("\n")[0];

// Assertions from the reference test are kept as evidence, never allowed to
// stop recording: a case written for the TypeSafe path may assert on
// `provider: "typesafe"` or on per-tool fail-closed output that `compatible`
// reports as an envelope error instead.
export const assert = new Proxy(strictAssert, {
  apply(target, self, args) {
    return soft(() => Reflect.apply(target, self, args));
  },
  get(target, key) {
    const value = Reflect.get(target, key);
    return typeof value === "function" ? (...args) => soft(() => value.apply(target, args)) : value;
  },
});

function soft(fn) {
  try {
    return fn();
  } catch (error) {
    if (!(error instanceof strictAssert.AssertionError)) throw error;
    current?.assertion_failures.push(firstLine(error));
  }
}

export function test(name, fn) {
  const ordinal = ++registered;
  return nodeTest(name, async () => {
    current = { ordinal, name, calls: [], ports: [], assertion_failures: [] };
    try {
      await fn();
    } catch (error) {
      current.assertion_failures.push(`threw: ${firstLine(error)}`);
    }
    const { calls, ports, assertion_failures } = current;
    current = null;
    writeFixture(
      `mock/${String(ordinal).padStart(3, "0")}-${slug(name)}.json`,
      {
        id: `mock-${String(ordinal).padStart(3, "0")}`,
        source: { file: MOCK_FILE, test: name },
        classes: ["mock"],
        divergences: [],
        reference_assertions_failed_under_compatible: assertion_failures,
        calls,
      },
      ports,
    );
  });
}

// Same contract as the reference withMock (mock.test.mjs:14-56): answers may be
// a function of the parsed request; `response` overrides status, raw body,
// usage (null omits it), and model.
export async function withMock(answers, fn, extraEnv = {}, response = {}) {
  const fake = await startFake((request) => {
    const body = { answers: typeof answers === "function" ? answers(request.body) : answers };
    if (response.usage !== null) body.usage = response.usage ?? { input_tokens: 10, output_tokens: 10 };
    if (response.model !== undefined) body.model = response.model;
    return {
      status: response.status ?? 200,
      body: typeof response.raw === "string" ? response.raw : JSON.stringify(body),
    };
  });
  current.ports.push(fake.port);
  const env = {
    JEV_PROVIDER: "compatible",
    JEV_API_KEY: "compatible-test-key",
    JEV_API_BASE_URL: `${fake.url}/v1/systemone`,
    ...(typeof extraEnv === "function" ? extraEnv(fake.port) : extraEnv),
  };
  // The reference's `requests` view: what each test inspects.
  const requests = [];
  const client = await connect(env);
  const recording = {
    callTool: async ({ name, arguments: args }) => {
      const before = fake.exchanges.length;
      const result = await callTool(client, name, args);
      const exchanges = fake.exchanges.slice(before);
      for (const e of exchanges) {
        if (e.harness_error) throw new Error(`fake provider failed: ${e.harness_error}`);
        requests.push({
          method: e.request.method,
          path: e.request.path,
          headers: { authorization: e.request.authorization, "content-type": e.request.content_type },
          body: e.request.body,
        });
      }
      current.calls.push({
        env: recordEnv(env, fake.url),
        tool: name,
        arguments: args === undefined ? {} : JSON.parse(JSON.stringify(args)),
        exchanges,
        result,
      });
      if ("protocol_error" in result) throw new Error(result.protocol_error.message);
      return result;
    },
  };
  try {
    return await fn(recording, requests);
  } catch (error) {
    current.assertion_failures.push(`threw: ${firstLine(error)}`);
  } finally {
    await client.close();
    await fake.close();
  }
}
