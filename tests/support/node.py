"""Node 24 as the ground truth for JS behavior (ADR-0006 differential tests).

CI provides Node 24 in the parity stage. Locally the tests skip without it, unless `CI` is set,
where a missing or wrong Node fails instead of silently passing.
"""

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from jev_judge_mcp.serialize import UNDEFINED

REPO_ROOT = Path(__file__).resolve().parents[2]
ORACLE = REPO_ROOT / "tests/parity/harness/js_oracle.mjs"
_PINNED = Path.home() / ".nvm/versions/node/v24.19.0/bin/node"


def find_node() -> str:
    """The Node 24 binary: `$NODE`, then the Makefile's pinned path, then `node` on PATH."""
    for candidate in (os.environ.get("NODE"), str(_PINNED) if _PINNED.exists() else None, shutil.which("node")):
        if not candidate:
            continue
        version = subprocess.run([candidate, "--version"], capture_output=True, text=True, check=False).stdout
        if version.startswith("v24."):
            return candidate
    reason = "Node 24 not found (set NODE)"
    if os.environ.get("CI"):
        pytest.fail(reason)
    pytest.skip(reason)


def to_wire(value: object) -> object:
    """Encode what JSON cannot carry (non-finite numbers, undefined) in the oracle's escape objects."""
    if value is UNDEFINED:
        return {"__jev_undef__": 1}
    if isinstance(value, float) and not math.isfinite(value):
        return {"__jev_nf__": "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")}
    if isinstance(value, dict):
        return {key: to_wire(item) for key, item in cast("dict[str, object]", value).items()}
    if isinstance(value, list | tuple):
        return [to_wire(item) for item in cast("list[object]", value)]
    return value


def run_oracle(node: str, tasks: list[dict[str, object]]) -> list[str]:
    """Run every task through Node in one process and return its outputs in order."""
    stdin = "".join(json.dumps(to_wire(task), ensure_ascii=True) + "\n" for task in tasks)
    proc = subprocess.run([node, str(ORACLE)], input=stdin.encode(), capture_output=True, check=False, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode()
    outputs: list[str] = json.loads(proc.stdout.decode())
    assert len(outputs) == len(tasks)
    return outputs
