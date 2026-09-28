"""Render the architecture diagram from docs/architecture.json.

Viewer source: the installed Archify at ~/.agents/skills/archify — Archify
2.17.0-dev.1 plus local commits 12527a1 and 7c3ae9a (toolbar clearance), not
on upstream. The generator tag alone cannot tell these builds apart.

Archify embeds JetBrains Mono (SIL OFL-1.1). This repo ships no font, so the one
licence notice for the diagram stays Archify's MIT text (THIRD_PARTY_NOTICES.md).

HTML: `archify render` into a temp file, then the font block and family names are
stripped and the result replaces docs/architecture.html.

SVG: Archify has no SVG command. The README file is the viewer's own export:
headless Chrome opens the stripped page and clicks `button[data-format="svg"]`,
which runs `serializeSvg(1, { autoTheme: true })`. That string is
docs/readme/architecture.svg.
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIFY = Path.home() / ".agents/skills/archify/bin/archify.mjs"
HTML = ROOT / "docs/architecture.html"
SVG = ROOT / "docs/readme/architecture.svg"
SPEC = ROOT / "docs/architecture.json"
FONT_BLOCK = re.compile(r'(<style id="archify-fonts">).*?(</style>)', re.DOTALL)
FAMILY = "'JetBrains Mono', "
FONT_COMMENT = (
    "        // Keep the same font bytes, character coverage, and attribution in\n"
    "        // standalone SVGs and the SVG images used by every raster export.\n"
)
SYSTEM_COMMENT = "        // Exports use the system monospace stack. No font is embedded.\n"
CHROME_CANDIDATES = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
)

# Clicks the viewer's SVG export and prints the serialized document.
_EXPORT_JS = r"""
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');

const [chrome, htmlUrl, port, userData] = process.argv.slice(2);
const child = spawn(chrome, [
  '--headless=new',
  '--disable-gpu',
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${userData}`,
  'about:blank',
], { stdio: 'ignore' });

function get(path) {
  return new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port, path }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve(body));
    }).on('error', reject);
  });
}

async function waitReady() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      await get('/json/version');
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  throw new Error('Chrome did not open a debugging port');
}

async function main() {
  await waitReady();
  const target = await new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1',
      port,
      method: 'PUT',
      path: '/json/new?' + encodeURIComponent(htmlUrl),
    }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve(JSON.parse(body)));
    });
    req.on('error', reject);
    req.end();
  });
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let nextId = 0;
  const pending = new Map();
  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
    }
  });
  const send = (method, params) => new Promise((resolve) => {
    const id = ++nextId;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
  await new Promise((resolve) => ws.addEventListener('open', resolve));
  await send('Runtime.enable');
  await new Promise((resolve) => setTimeout(resolve, 800));
  const clicked = await send('Runtime.evaluate', {
    expression: `(() => {
      window.__svg = null;
      const orig = URL.createObjectURL;
      URL.createObjectURL = function (blob) {
        if (blob && String(blob.type).includes('svg')) blob.text().then((text) => { window.__svg = text; });
        return orig.call(URL, blob);
      };
      URL.revokeObjectURL = function () {};
      const button = document.querySelector('button[data-format="svg"]');
      if (!button) return 'no-button';
      button.click();
      return 'clicked';
    })()`,
    returnByValue: true,
  });
  if (!clicked.result || clicked.result.result.value !== 'clicked') {
    throw new Error('SVG export button missing');
  }
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const probe = await send('Runtime.evaluate', {
      expression: 'window.__svg || ""',
      returnByValue: true,
    });
    const text = probe.result && probe.result.result && probe.result.result.value;
    if (text) {
      process.stdout.write(text);
      child.kill();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error('SVG export produced no document');
}

main().catch((error) => {
  console.error(error);
  child.kill();
  process.exit(1);
});
"""


def _chrome() -> Path:
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit("Chrome is required to export the README SVG; Archify has no SVG command")


def _strip_font(html: str) -> str:
    stripped, blocks = FONT_BLOCK.subn(
        r"\1\n/* No font is embedded. Text uses the system monospace stack. */\n  \2",
        html,
    )
    if blocks != 1:
        raise SystemExit(f"expected one archify-fonts block, found {blocks}")
    return stripped.replace(FAMILY, "").replace(FONT_COMMENT, SYSTEM_COMMENT)


def _node() -> str:
    found = shutil.which("node")
    if found is None:
        raise SystemExit("node is required to render the diagram")
    return found


def _render_html() -> str:
    if not ARCHIFY.is_file():
        raise SystemExit(f"Archify is not installed at {ARCHIFY}")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "architecture.html"
        subprocess.run(  # noqa: S603
            [_node(), str(ARCHIFY), "render", "architecture", str(SPEC), str(out), "--quality", "showcase"],
            check=True,
        )
        return _strip_font(out.read_text(encoding="utf-8"))


def _export_svg(html_url: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "export-svg.cjs"
        script.write_text(_EXPORT_JS, encoding="utf-8")
        port = "9341"
        completed = subprocess.run(  # noqa: S603
            [_node(), str(script), str(_chrome()), html_url, port, str(Path(tmp) / "chrome")],
            check=True,
            capture_output=True,
        )
    text = completed.stdout.decode("utf-8")
    if not text.startswith("<?xml"):
        raise SystemExit("SVG export did not return an XML document")
    return text


def main() -> int:
    html = _render_html()
    HTML.write_text(html, encoding="utf-8")
    svg = _export_svg(HTML.resolve().as_uri())
    SVG.write_text(svg, encoding="utf-8")
    if "@font-face" in html or "JetBrains Mono" in html or "@font-face" in svg or "JetBrains Mono" in svg:
        print("render left an embedded font", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
