// Shared recording/replay machinery: the fake provider, one MCP call against
// the pinned reference server, and deterministic fixture output.
import { createServer } from "node:http";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const NODE_VERSION = "v24.19.0";
export const PARITY_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const REFERENCE_DIR = path.join(PARITY_DIR, "reference");
export const FIXTURES_DIR = path.join(PARITY_DIR, "fixtures");
export const SERVER_PATH = path.join(REFERENCE_DIR, "dist", "index.js");

// Placeholder for the fake's origin in recorded env values; the replayer
// substitutes its own. Ports never reach a fixture.
export const FAKE_URL = "{{FAKE_URL}}";

if (process.version !== NODE_VERSION) {
  throw new Error(`parity harness needs Node ${NODE_VERSION}, running ${process.version}`);
}

const sdk = (file) =>
  import(pathToFileURL(path.join(REFERENCE_DIR, "node_modules/@modelcontextprotocol/sdk/dist/esm", file)).href);
const { Client } = await sdk("client/index.js");
const { StdioClientTransport } = await sdk("client/stdio.js");

// The fake provider speaks the compatible envelope. `respond(request)` returns
// {status, body} where body is the exact response text. Every exchange is kept
// in order: the parsed request as the reference sent it, and the raw response.
export async function startFake(respond) {
  const exchanges = [];
  const http = createServer((req, res) => {
    let raw = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      const request = {
        method: req.method,
        path: req.url,
        authorization: req.headers.authorization ?? null,
        content_type: req.headers["content-type"] ?? null,
        body: JSON.parse(raw),
      };
      let response;
      try {
        response = respond(request);
        exchanges.push({ request, response });
      } catch (error) {
        response = { status: 599, body: `fake provider error: ${error.message}` };
        exchanges.push({ request, response, harness_error: error.message });
      }
      res.writeHead(response.status, { "Content-Type": "application/json" });
      res.end(response.body);
    });
  });
  await new Promise((resolve, reject) => {
    http.once("error", reject);
    http.listen(0, "127.0.0.1", resolve);
  });
  const { port } = http.address();
  return {
    port,
    url: `http://127.0.0.1:${port}`,
    exchanges,
    close: () => new Promise((resolve) => http.close(resolve)),
  };
}

// Start the reference server with exactly `env` (plus the MCP SDK's default
// HOME/PATH/SHELL/TERM/USER/LOGNAME), so no host credential leaks in.
export async function connect(env) {
  const client = new Client({ name: "jev-mcp-parity", version: "0.0.0" });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env,
    stderr: "ignore",
  });
  await client.connect(transport);
  return client;
}

// One tool call. A JSON-RPC level failure is recorded rather than thrown.
export async function callTool(client, tool, args) {
  try {
    return await client.callTool({ name: tool, arguments: args });
  } catch (error) {
    return { protocol_error: { code: error.code ?? null, message: error.message } };
  }
}

// Env values carry the fake origin; record them with the placeholder.
export function recordEnv(env, fakeUrl) {
  const out = {};
  for (const key of Object.keys(env).sort()) {
    const value = env[key];
    out[key] = fakeUrl && typeof value === "string" ? value.split(fakeUrl).join(FAKE_URL) : value;
  }
  return out;
}

export function expandEnv(env, fakeUrl) {
  const out = {};
  for (const [key, value] of Object.entries(env)) out[key] = value.split(FAKE_URL).join(fakeUrl ?? FAKE_URL);
  return out;
}

export function serialize(value) {
  return JSON.stringify(value, null, 2) + "\n";
}

// Write one fixture, refusing anything that leaked a live port.
export function writeFixture(relative, fixture, ports) {
  const text = serialize(fixture);
  for (const port of ports) {
    if (text.includes(`127.0.0.1:${port}`)) throw new Error(`${relative}: fixture leaks fake port ${port}`);
  }
  const file = path.join(FIXTURES_DIR, relative);
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, text);
  return file;
}

export function slug(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80)
    .replace(/-+$/g, "");
}
