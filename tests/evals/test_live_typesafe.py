"""Live L3 smoke against the real TypeSafe API (marker `live`, `make eval-live`).

Deselected by default so `make eval` and `make ci` stay offline; when selected, a missing
TYPESAFE_API_KEY fails instead of skipping. Each run is bounded by the synthetic `live-*` datasets
(6 requests in total here) and the runner's `LIVE_REQUEST_CAP`. The key is read by the server's own
settings and never appears in assertions or output.
"""

import json
import os
from pathlib import Path
from typing import cast

import pytest

from evals.runners import live
from evals.runners.manifest import EVALS_DIR, load_manifest
from evals.runners.score import score

pytestmark = pytest.mark.live

LIVE_MANIFESTS = [EVALS_DIR / "manifests" / f"live-{name}.json" for name in ("classify", "verify")]


@pytest.fixture
def typesafe(monkeypatch: pytest.MonkeyPatch) -> None:
    assert os.environ.get("TYPESAFE_API_KEY"), "live evals need TYPESAFE_API_KEY exported; this is not a skip"
    monkeypatch.setenv("JEV_PROVIDER", "typesafe")


@pytest.mark.parametrize("manifest_path", LIVE_MANIFESTS, ids=lambda path: path.stem)
@pytest.mark.usefixtures("typesafe")
def test_live_run_records_scoreable_outputs_from_the_pinned_model(
    manifest_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = load_manifest(manifest_path)
    monkeypatch.setenv("JEV_MCP_MODEL", manifest.model)
    out = tmp_path / "outputs.jsonl"

    assert live.main([str(manifest_path), str(out)], {live.LIVE_FLAG: "1"}) == 0

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in rows] == [case.id for case in live.load_cases(manifest.dataset)]
    assert all("output" in row for row in rows), [row.get("error") for row in rows]
    assert {(row["output"]["provider"], row["output"]["model"]) for row in rows} == {("typesafe", manifest.model)}
    report = score(manifest, out, "all")
    metrics = cast(dict[str, object], report["metrics"])
    # Plumbing, not quality: every answer validated and is scoreable. No accuracy claim is made here.
    assert metrics["auto_coverage"] is not None, report
    assert metrics.get("invalid", 0) == 0, report
