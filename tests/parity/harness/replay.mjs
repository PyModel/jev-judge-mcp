// Replay every committed fixture against the pinned reference (make parity-verify).
// The fake serves each call's recorded responses in order and requires the
// reference to send the recorded requests; the tool result must be
// byte-identical. This is the same replay a Python parity test performs.
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { callTool, connect, expandEnv, FIXTURES_DIR, serialize, startFake } from "./lib.mjs";

const files = [];
const walk = (dir) => {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (entry.name.endsWith(".json") && entry.name !== "index.json") files.push(full);
  }
};
walk(FIXTURES_DIR);
files.sort();

const failures = [];
let calls = 0;
for (const file of files) {
  const fixture = JSON.parse(readFileSync(file, "utf8"));
  for (const [i, call] of fixture.calls.entries()) {
    calls += 1;
    const problem = await replay(call);
    if (problem) failures.push(`${path.relative(FIXTURES_DIR, file)} call ${i}: ${problem}`);
  }
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  console.error(`parity-verify: ${failures.length} of ${calls} calls differ`);
  process.exit(1);
}
console.log(`parity-verify: ${calls} calls in ${files.length} fixtures replay byte-identically`);

async function replay(call) {
  let next = 0;
  const fake = await startFake((request) => {
    const recorded = call.exchanges[next++];
    if (!recorded) throw new Error("unexpected extra request");
    const { body, ...head } = request;
    const { body: recordedBody, ...recordedHead } = recorded.request;
    if (serialize(head) !== serialize(recordedHead)) throw new Error(`request head differs: ${serialize(head)}`);
    if (serialize(body) !== serialize(recordedBody)) throw new Error("request body differs");
    return recorded.response;
  });
  const client = await connect(expandEnv(call.env, fake.url));
  let result;
  try {
    result = await callTool(client, call.tool, call.arguments);
  } finally {
    await client.close();
    await fake.close();
  }
  const harnessError = fake.exchanges.find((e) => e.harness_error);
  if (harnessError) return harnessError.harness_error;
  if (fake.exchanges.length !== call.exchanges.length) {
    return `made ${fake.exchanges.length} requests, recorded ${call.exchanges.length}`;
  }
  if (serialize(result) !== serialize(call.result)) return "tool result differs";
  return null;
}
