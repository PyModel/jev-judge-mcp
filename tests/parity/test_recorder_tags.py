"""Rebuild fixture tags with the recorder's Node code, without running the reference server."""

import json
import subprocess

from tests.support.fixtures import divergences, iter_fixtures
from tests.support.node import REPO_ROOT, find_node


def test_recorder_reconstructs_committed_program_tags() -> None:
    fixtures = list(iter_fixtures())
    inputs = [
        {
            "id": fixture.id,
            "calls": [None] * len(fixture.payload["calls"]),
            "divergences": [tag for tag in divergences(fixture) if tag != "ADR-0062"],
        }
        for fixture in fixtures
    ]
    script = """
        import { readFileSync } from "node:fs";
        import { fixtureTags } from "./tests/parity/harness/fixture-tags.mjs";
        console.log(JSON.stringify(JSON.parse(readFileSync(0, "utf8")).map(fixtureTags)));
    """
    result = subprocess.run(
        [find_node(), "--input-type=module", "-e", script],
        cwd=REPO_ROOT,
        input=json.dumps(inputs),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert json.loads(result.stdout) == [list(divergences(fixture)) for fixture in fixtures]
