// Ground truth for the jev_extract candidate pipeline (ADR-0004). Reads NDJSON tasks
// {document, pattern, flags} on stdin and writes a JSON array of results. The pipeline is the
// reference worker's (index.ts:894-910 at 69ffb4b), run inline: the corpus has no slow patterns.
import { readFileSync } from "node:fs";

const MAX_CANDIDATES = 20;
const MAX_CANDIDATE_CHARS = 2000;

const out = [];
for (const line of readFileSync(0, "utf8").split("\n")) {
  if (!line) continue;
  const { document, pattern, flags } = JSON.parse(line);
  try {
    const re = new RegExp(pattern, flags);
    const seen = new Set();
    const candidates = [];
    let truncated = false;
    let tooLong = 0;
    for (const match of document.matchAll(re)) {
      const value = match[0];
      if (value.length === 0 || seen.has(value)) continue;
      seen.add(value);
      if (value.length > MAX_CANDIDATE_CHARS) { tooLong += 1; continue; }
      if (candidates.length >= MAX_CANDIDATES) { truncated = true; break; }
      candidates.push(value);
    }
    out.push({ candidates, truncated, tooLong, error: null });
  } catch (error) {
    out.push({ candidates: [], truncated: false, tooLong: 0, error: String(error.message) });
  }
}
process.stdout.write(JSON.stringify(out));
