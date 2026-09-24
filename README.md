<div align="center">

<img src="./docs/readme/banner.svg" alt="jev-judge-mcp, with a scales-of-justice emblem: typed judgment tools for MCP agents. Model judges, policy decides: auto, review, or escalate. Check, find, decide, ship." width="100%" />

<p>
  <a href="https://github.com/PyModel/jev-judge-mcp/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/PyModel/jev-judge-mcp/ci.yml?branch=main&style=for-the-badge&label=CI&logo=githubactions&logoColor=white&labelColor=0a0a0f"></a>
  <a href="./pyproject.toml"><img alt="Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FPyModel%2Fjev-judge-mcp%2Fmain%2Fpyproject.toml&query=%24.project.version&prefix=v&label=version&style=for-the-badge&labelColor=0a0a0f&color=ffc94d"></a>
  <a href="https://www.python.org/"><img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-ff7b2e?style=for-the-badge&logo=python&logoColor=white&labelColor=0a0a0f"></a>
  <a href="https://modelcontextprotocol.io/"><img alt="MCP over stdio" src="https://img.shields.io/badge/MCP-stdio-b9e769?style=for-the-badge&logo=modelcontextprotocol&logoColor=white&labelColor=0a0a0f"></a>
  <a href="./LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-4fd8c4?style=for-the-badge&logo=opensourceinitiative&logoColor=white&labelColor=0a0a0f"></a>
</p>

</div>

An MCP server that gives your coding agent eleven judgment tools backed by TypeSafe's Jev model. The agent hands a tool some evidence and a question it can enumerate: is this claim supported, is this page safe to read, which of these files answers the question, did this patch finish the task. Jev answers with probabilities, usually in under a second. Policy turns them into one of three actions: `auto` (proceed), `review` (check it another way), or `escalate` (stop).

Use it for checks that have a fixed set of answers. When the step needs new text, code, or options you cannot list, the agent should write it itself.

## Install

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), a POSIX system (Linux or macOS), and a TypeSafe API key from [console.typesafe.ai](https://console.typesafe.ai/settings/keys). The published package is on PyPI, and these three commands configure your agents to launch that pinned package (ADR-0051):

```sh
uvx --from 'jev-judge-mcp[typesafe]' jev-judge-mcp setup     # verify your key, then store it
uvx --from 'jev-judge-mcp[typesafe]' jev-judge-mcp install   # add the server to your agents
uvx --from 'jev-judge-mcp[typesafe]' jev-judge-mcp doctor    # check the configuration, offline
```

Every entry the installer writes also requests a Python the package itself declares — `--python '>=3.12'`, taken from the package's `Requires-Python` metadata (ADR-0053). On a machine whose first interpreter is older (Ubuntu 22.04's 3.10, macOS system 3.9), uv picks or downloads one that qualifies instead of refusing to start the server.

<details>
<summary><b>More installer options</b></summary>

To develop against an unreleased tree, install from a clone and pass `--from-checkout`, which pins the entries at your checkout instead of the PyPI package:

```sh
git clone https://github.com/PyModel/jev-judge-mcp
cd jev-judge-mcp
uv sync --extra typesafe
uv run jev-judge-mcp install --from-checkout
```

That extra is only for running the server. Development and `make typecheck` need the full sync:
`uv sync --locked --all-extras` — a plain `uv sync` fails `make typecheck` with confusing
`Import "typesafe_sdk" could not be resolved` errors.

Running `install` from an unreleased clone without `--from-checkout`? It prints a note that the pinned PyPI build does not include your local changes, and points here. The post-write check then exercises the published build, not your tree.

Installed from a clone earlier? One plain re-run of `install` rewrites the entries this installer owns to the version-pinned PyPI spec — that is the whole migration. Entries the installer does not own are left alone.

Restart your agent. The tools show up as `jev_verify`, `jev_gate`, and so on (`mcp__jev__*` in Claude Code).

`setup` reads the key from `TYPESAFE_API_KEY`, or asks for it at a hidden prompt. It never takes the key as an argument, so the key stays out of your shell history. It makes one live call to check the key and writes nothing if TypeSafe rejects it. A good key goes to `~/.config/jev-mcp/key`, readable only by you. The server uses that file whenever `TYPESAFE_API_KEY` is unset, so agents you start without exporting the key still work. When the variable is set, it wins.

`install` finds the agents on your machine, shows what it will change, and asks before writing. It supports Claude Code, Claude Desktop, Codex (CLI and the ChatGPT app), Cursor, OpenCode, Pi, omp, and Pythinker.

```sh
uvx --from 'jev-judge-mcp[typesafe]' jev-judge-mcp install --dry-run    # show the plan, write nothing
uvx --from 'jev-judge-mcp[typesafe]' jev-judge-mcp install -a claude-code    # one agent (repeatable)
uvx --from 'jev-judge-mcp[typesafe]' jev-judge-mcp install --remove    # undo what install wrote
```

Terminal agents get a reference to `TYPESAFE_API_KEY`, never the key itself. Desktop apps don't see your shell's environment. Claude Desktop (macOS only) is skipped unless you pass `--desktop-key`, which writes the key into that app's config file. The same flag writes the key into the Codex and Pythinker files when the ChatGPT or Pythinker desktop app shares them; without it, `install` says that app has no key. The installer warns if a file holding the key ends up readable by other users. Pi also needs its MCP adapter first: `pi install npm:pi-mcp-adapter`.

</details>

<details>
<summary><b>Register the server by hand</b></summary>

`<uvx>` is the absolute path of `uvx`. `<spec>` is `jev-judge-mcp[typesafe]==<version>` (the version-pinned PyPI package, what `install` writes by default) or your checkout's absolute path plus `[typesafe]`, for example `/home/me/jev-judge-mcp[typesafe]`. `--python '>=3.12'` is what `install` derives from the package metadata; keep it when you register by hand.

Claude Code (`~/.claude.json`), omp (`~/.omp/agent/mcp.json`), Cursor (`~/.cursor/mcp.json`), and Pi (`~/.pi/agent/mcp.json`) use the same shape. Claude Code and omp also add `"type": "stdio"`. Pi also adds the three exposure keys below; without them `pi-mcp-adapter` keeps the server lazy and proxy-only and the tools stay out of the model's initial list (ADR-0036).

```json
{
  "mcpServers": {
    "jev": {
      "command": "<uvx>",
      "args": ["--python", ">=3.12", "--from", "<spec>", "jev-judge-mcp"],
      "env": {"TYPESAFE_API_KEY": "${TYPESAFE_API_KEY}"}
    }
  }
}
```

Pi's full entry:

```json
{
  "mcpServers": {
    "jev": {
      "command": "<uvx>",
      "args": ["--python", ">=3.12", "--from", "<spec>", "jev-judge-mcp"],
      "env": {"TYPESAFE_API_KEY": "${TYPESAFE_API_KEY}"},
      "lifecycle": "eager",
      "directTools": true,
      "toolPrefix": "none"
    }
  }
}
```

`lifecycle: "eager"` connects at startup, `directTools: true` registers every tool individually, and `toolPrefix: "none"` keeps the published names (`jev_verify`, ...), so the tools sit in the model's initial tool list, callable like any builtin.

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`) uses the same shape with the key itself in `env`. Pythinker (`~/.pythinker-code/mcp.json`) uses it without `env`, unless the Pythinker desktop app shares the file and needs the key there.

Codex CLI and the ChatGPT app share `~/.codex/config.toml`:

```toml
[mcp_servers.jev]
command = "<uvx>"
args = ["--python", ">=3.12", "--from", "<spec>", "jev-judge-mcp"]
env_vars = ["TYPESAFE_API_KEY"]
```

When the ChatGPT app shares that file, it also needs the key itself in an `[mcp_servers.jev.env]` table with `TYPESAFE_API_KEY = "<key>"`.

OpenCode (`~/.config/opencode/opencode.json`):

```json
{
  "mcp": {
    "jev": {
      "type": "local",
      "command": ["<uvx>", "--python", ">=3.12", "--from", "<spec>", "jev-judge-mcp"],
      "environment": {"TYPESAFE_API_KEY": "{env:TYPESAFE_API_KEY}"}
    }
  }
}
```

</details>

## What to use it for

Ask your agent in plain words. It picks the tool, or you can name it.

| You want to | Tool | You get |
|---|---|---|
| Check that the agent's "done" matches the diff and the test log | `jev_gate` | one ship decision over the patch and each completion claim |
| Check claims in a summary or PR description against the sources | `jev_verify` | verified, contradicted, or unsupported for each claim |
| Screen a fetched web page for prompt injection before reading it | `jev_screen` | pass, review, block, or skip |
| Review a patch against the request | `jev_review` | correctness, spec match, test gaps, blast radius |
| Spot drift between docs and code, or a changelog and a diff | `jev_compare` | same fact, contradiction, or different facts |
| Find the file or note that answers a question | `jev_find` | the best match, plus whether anything matches at all |
| Rank search hits or grep results | `jev_rerank` | a relevance score for every candidate, sorted |
| Route tickets or label many items at once | `jev_classify` | one class per item from your catalog |
| Pick one option, or decide whether to keep waiting on a slow command | `jev_decide` | your option, or `ask_user` / `investigate` / `none` |
| Grade severity or risk on your own scale | `jev_score` | a position on your 2 to 10 levels, with the distribution; threshold it in code, since positions between levels are weakly calibrated |
| Pull a version, date, or price out of a document | `jev_extract` | a value copied from a match of your regex, or null |

For example, "use jev_verify to check your summary against the changelog" returns one row per claim:

```json
{
  "claim": "The setup command accepts the API key as a command-line argument.",
  "verdict": "contradicted",
  "probabilities": { "supports": 0, "contradicts": 1, "says_nothing": 0 },
  "confidence": 1,
  "action": "auto",
  "supporting_evidence": "setup.py"
}
```

Jev sees only what the agent passes in the call, so the agent has to include the evidence. [`docs/skills/jev-mcp/SKILL.md`](docs/skills/jev-mcp/SKILL.md) is a skill you can give your agent: it covers which tool fits which step and what to do with each action. Allow rules for Claude Code are printed by `doctor`, and opt-in setups for Claude Code, Codex, and Pi are in [the harness samples](docs/harness/).

## Configuration

The server reads environment variables only. It does not load a `.env` file.

| Variable | Default | What it does |
|---|---|---|
| `TYPESAFE_API_KEY` | unset | TypeSafe key; takes priority over the stored key |
| `JEV_MCP_KEY_FILE` | `~/.config/jev-mcp/key` | where `setup` stores the key and the server reads it |
| `JEV_PROVIDER` | `auto` | `auto` takes the first provider with credentials: typesafe, openrouter, cloudflare, compatible. The reference's vercel provider (`AI_GATEWAY_API_KEY`) is not supported |
| `JEV_MCP_MODEL` | `jev-latest` | Jev model to ask |
| `JEV_MCP_CACHE` | off | replay identical requests from disk at no API cost; leave it off when answers must be fresh, and delete the directory to clear it |
| `JEV_MCP_CACHE_DIR` | `~/.cache/jev-mcp` | where the cache lives |
| `JEV_MCP_TRANSPORT` | `stdio` | `streamable-http` is experimental and binds `JEV_MCP_HTTP_HOST:JEV_MCP_HTTP_PORT`, default `127.0.0.1:8000`. Port 8000 is often already taken |
| `JEV_MCP_HTTP_TOKEN` | unset | bearer token for the HTTP transport; required on every request, and required for any non-loopback `JEV_MCP_HTTP_HOST` |
| `JEV_MCP_LOG_LEVEL` | `INFO` | logs go to stderr |

## Operator notes

Two parity-sanctioned facts — the reference server behaves the same way — that show up as cost
or latency:

- `jev_verify` and `jev_screen` put no length bound on their input. The claims, evidence, or page
text are sent to the provider in one request, however large, so token cost and latency scale with
what the caller passes. Bound the text at the call site when it is not yours.
- Requests over stdio carry no provider deadline (the sanctioned divergence
`stdio-no-provider-deadline`): a provider that stops answering keeps the tool call waiting until
the client cancels it.
- `initialize`'s `serverInfo.version`, the one startup log line, and `jev-judge-mcp --version`
  report the same build. A wheel, and a checkout whose HEAD is the tag `v<version>`, report that
  version. Any other git checkout reports `<version>+g<short sha>` (ADR-0054). The wire name stays
  `jev-mcp` (ADR-0049).
- `jev_rerank` returns every candidate in `ranked`, highest `relevance` first. `relevance` is that
  candidate's probability, to four decimal places. The response has no spread field. A flat band of
  low values means the candidates were not distinguishable: treat the order as weak, and read
  `ranked[].relevance` rather than the rank numbers.

### Running over HTTP (experimental)

The HTTP transport is Tier B experimental: it has no reliability contract and no admission control, and it is not covered by the parity suite. Only `127.0.0.1`, `localhost`, and `::1` are exempt from the token: exactly those hosts get the SDK's automatic Host/Origin validation. Every other host — `127.9.9.9`, `0:0:0:0:0:0:0:1`, `0.0.0.0`, a LAN address, a hostname — refuses to start unless `JEV_MCP_HTTP_TOKEN` is set, because every tool call would otherwise spend your provider key on behalf of anyone who can reach the port.

Port 8000, the default of `JEV_MCP_HTTP_PORT`, is often already taken (a local model server, another dev server). If it is, the process exits non-zero with one line naming `JEV_MCP_HTTP_PORT` and the port, and it leaves no listener behind (ADR-0055). Set the variable to a free port.

Generate a token:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Run the server with it:

```sh
JEV_MCP_TRANSPORT=streamable-http JEV_MCP_HTTP_HOST=0.0.0.0 JEV_MCP_HTTP_TOKEN=<token> jev-judge-mcp
```

Every HTTP request must then carry the token; a request without it, or with a wrong one, gets `401` before any tool runs:

```sh
curl -H "Authorization: Bearer <token>" \
  -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"example","version":"0"}}}' \
  http://127.0.0.1:8000/mcp
```

## Architecture

One diagram covers the whole server: the tool-call loop from `tools/call` to the returned action text, the fail-closed answer path, and the local CLI commands around it (`install`, `setup`, `hook gate`, `doctor`) with the stored key file and the optional response cache. Open [`docs/architecture.html`](docs/architecture.html) for the interactive version (guided views, dark mode, node search, relationship tracing).

<p align="center">
  <img src="docs/readme/architecture.svg" alt="jev-judge-mcp architecture: an MCP client calls eleven Jev tools through one Toolset over stdio or streamable-http; the runtime resolves one of four providers, validation rejects malformed answers, pure policy decides auto, review, or escalate, and the result serializes back to the client. The install, setup, hook, and doctor CLIs, the stored key file, and the optional response cache surround the server." width="880">
</p>

## Measured results

Two paid studies, both descriptive, with small samples and no significance test.

On 150 questions with Pi (`opencode-go/deepseek-v4.1-flash`), forcing a Jev call added 10.4 s median wall time per task. Letting the agent choose left Jev uncalled on all 150. Jev itself answered in 465 ms median over 157 calls. Accuracy was not measured. Details: [`evals/reports/bench150.md`](evals/reports/bench150.md).

| arm | median wall s | p95 | called Jev | agent spend |
|---|---|---|---|---|
| A direct | 3.06 | 6.17 | 0/150 | $0.0928 |
| B automatic | 2.91 | 8.09 | 0/150 | $0.0955 |
| C forced | 13.95 | 28.67 | 150/150 | $0.2904 |

The agent outcome study ran on 2026-09-23 with `jev-1.13.0`: three tasks, three repeats per arm, with and without Jev. Both agents solved the same pairs either way and picked the right decision on every run. Both were slower with Jev. One Pi pair is excluded because its with-Jev run never called Jev. Details, raw records, and the chart script: [`docs/evals/`](docs/evals/README.md).

| agent | solved without / with Jev | median time to correct, without / with | extra wall time with Jev (paired median) | spend |
|---|---|---|---|---|
| Claude Code (`claude-sonnet-5`) | 6/9 / 6/9 | 14.6 s / 18.3 s | +4.6 s | $1.4953 |
| Pi (`ds4/glm-5.3-flash`, local) | 6/8 / 6/8 | 49.4 s / 127.8 s | +85.8 s | $0.0006 |

<p align="center">
  <img src="docs/evals/charts/agent-outcomes-claude.svg" alt="Claude Code outcome study: tasks solved, median time to a correct solution, tokens per solved task, and the paired wall-time difference, with and without Jev" width="820">
</p>
<p align="center">
  <img src="docs/evals/charts/agent-outcomes-pi.svg" alt="Pi outcome study: tasks solved, median time to a correct solution, tokens per solved task, and the paired wall-time difference, with and without Jev" width="820">
</p>

## About this project

This is a Python rewrite of the TypeScript `@jkudish/jev-mcp` 0.5.0. The ten reference tools match it on the wire, checked by recorded parity fixtures; `jev_score` is an addition. Vocabulary is in [`docs/CONTEXT.md`](docs/CONTEXT.md), decisions in [`docs/adr/`](docs/adr/), and security notes in [`SECURITY.md`](SECURITY.md). Windows is not supported; the server exits at startup on a non-POSIX platform.

```sh
uv sync --locked --all-extras   # development needs every extra; --extra typesafe alone only runs the server
make ci      # lint, types, unit, property, policy coverage, contract, parity, security, build, smoke, eval
```

`make eval-live`, `make security-live`, and `JEV_AB_LIVE=1 make ab` call paid services and stay off CI. Contribution notes are in [`CONTRIBUTING.md`](CONTRIBUTING.md).

MIT license.
