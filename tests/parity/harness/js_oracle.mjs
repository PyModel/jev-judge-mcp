// Ground truth for the Python serializer (ADR-0006). Reads NDJSON tasks on stdin and writes a
// JSON array of results. A single-key object {"__jev_nf__": "NaN" | "Infinity" | "-Infinity"}
// stands for that number, and {"__jev_undef__": 1} for undefined, since JSON has neither.
import { readFileSync } from "node:fs";

const revive = (_key, value) => {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const keys = Object.keys(value);
    if (keys.length === 1 && keys[0] === "__jev_nf__") return Number(value.__jev_nf__);
    if (keys.length === 1 && keys[0] === "__jev_undef__") return undefined;
  }
  return value;
};

const out = [];
for (const line of readFileSync(0, "utf8").split("\n")) {
  if (!line) continue;
  const task = JSON.parse(line, revive);
  if (task.op === "stringify") out.push(JSON.stringify(task.value, null, 2));
  else if (task.op === "toString") out.push(String(task.x));
  else if (task.op === "toFixed") out.push(task.x.toFixed(task.digits));
  else throw new Error(`unknown op ${task.op}`);
}
process.stdout.write(JSON.stringify(out));
