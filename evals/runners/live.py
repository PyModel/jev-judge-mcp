"""Live L3 collection: `JEV_EVAL_LIVE=1 python -m evals.runners.live MANIFEST OUT [--repeats N]`.

Calls the real tools through the configured provider, so it costs money and drifts with the model.
It refuses to start unless `JEV_EVAL_LIVE=1` is passed in its environment, unless `JEV_PROVIDER` is
`typesafe`, unless the server's resolved model is the manifest's pinned model, and when cases x repeats
exceeds `LIVE_REQUEST_CAP`. Every tool call makes at most one provider request, so the cap bounds
requests before any is sent; a result that reports another model aborts the run. Recorded outputs feed
`evals.runners.score`. The flag is eval-only and stays out of `jev_judge_mcp.settings` (ADR-0008/0017).
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import anyio

from evals.calibration.flips import MAX_REPEATS
from evals.runners.manifest import Manifest, load_cases, load_manifest

LIVE_FLAG = "JEV_EVAL_LIVE"
LIVE_REQUEST_CAP = 25
"""Hard ceiling on tool calls (each at most one provider request) per live run."""


class LiveRunRefusedError(RuntimeError):
    pass


def require_live_enabled(environ: Mapping[str, str]) -> None:
    """Raise unless the environment explicitly opts in with `JEV_EVAL_LIVE=1`."""
    if environ.get(LIVE_FLAG) != "1":
        raise LiveRunRefusedError(f"live evals call a paid provider; set {LIVE_FLAG}=1 to run them")


def require_pinned_model(manifest: Manifest, resolved_model: str) -> None:
    """The server must resolve to exactly the model the manifest pins; `jev-latest` drifts."""
    if resolved_model != manifest.model:
        raise LiveRunRefusedError(f"server model {resolved_model!r} is not the pinned {manifest.model!r}")


def require_typesafe(provider: str) -> None:
    """Live evals run only against TypeSafe: `auto` could resolve to another provider (evals/README.md)."""
    if provider.lower() != "typesafe":
        raise LiveRunRefusedError(f"JEV_PROVIDER={provider!r}; live evals run only against 'typesafe'")


def require_within_cap(cases: int, repeats: int) -> None:
    if cases * repeats > LIVE_REQUEST_CAP:
        raise LiveRunRefusedError(f"{cases} cases x {repeats} repeats exceeds the {LIVE_REQUEST_CAP}-request cap")


async def collect(manifest: Manifest, out: Path, repeats: int) -> None:
    # Imported here so the flag refusal never loads settings, and no refusal builds a runtime or a provider.
    from jev_judge_mcp.settings import load_settings
    from jev_judge_mcp.tools import TOOLS
    from jev_judge_mcp.tools.base import Runtime
    from jev_judge_mcp.tools.toolset import Toolset

    settings = load_settings()
    require_typesafe(settings.jev_provider)
    cases = load_cases(manifest.dataset)
    require_within_cap(len(cases), repeats)
    toolset = Toolset(Runtime(settings), TOOLS)
    try:
        require_pinned_model(manifest, toolset.runtime.model)
        with out.open("w", encoding="utf-8") as sink:
            for case in cases:
                for repeat in range(repeats):
                    result = await toolset.call(manifest.tool, case.input)
                    text = result.content[0].text if result.content and result.content[0].type == "text" else ""
                    row: dict[str, object] = {"id": case.id, "repeat": repeat}
                    if result.is_error:
                        row["error"] = text
                    else:
                        row["output"] = output = json.loads(text)
                        require_pinned_model(manifest, str(output.get("model")))
                    sink.write(json.dumps(row) + "\n")
    finally:
        await toolset.aclose()


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.runners.live", description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--repeats", type=int, default=1, choices=range(1, MAX_REPEATS + 1))
    args = parser.parse_args(argv)
    try:
        require_live_enabled(environ)
        manifest = load_manifest(args.manifest)
        anyio.run(collect, manifest, args.out, args.repeats)
    except LiveRunRefusedError as refusal:
        sys.stderr.write(f"refused: {refusal}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
