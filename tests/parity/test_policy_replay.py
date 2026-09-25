"""Recorded answers through policy/ must reproduce the recorded actions.

A drift in claim_action or verify_action fails this target. Additive fields are not actions.
"""

import json

from jev_judge_mcp.policy.actions import require_complete_context
from jev_judge_mcp.policy.claims import claim_action
from jev_judge_mcp.policy.thresholds import DEFAULT_AUTO_ACCEPT, DEFAULT_REVIEW_AT_CAP
from tests.support.fixtures import iter_calls


def test_recorded_claim_actions_match_policy() -> None:
    mismatches: list[str] = []
    for call in iter_calls():
        if call.payload["tool"] != "jev_gate" or call.payload["result"].get("isError"):
            continue
        text = call.payload["result"]["content"][0]["text"]
        if not text.startswith("{"):
            continue
        payload = json.loads(text)
        results = payload.get("verification", {}).get("results", [])
        auto_accept = payload.get("verification", {}).get("thresholds", {}).get("auto_accept", DEFAULT_AUTO_ACCEPT)
        review_at = payload.get("verification", {}).get("thresholds", {}).get("review_at", DEFAULT_REVIEW_AT_CAP)
        truncated = bool(payload.get("truncated"))
        for index, row in enumerate(results):
            if row.get("status") == "invalid_response" or row.get("verdict") is None:
                continue
            expected = require_complete_context(
                claim_action(row["verdict"], row.get("confidence"), auto_accept, review_at), truncated
            )
            if row.get("action") != expected:
                mismatches.append(f"{call.id}#{index}: recorded {row.get('action')} policy {expected}")
    assert mismatches == []
