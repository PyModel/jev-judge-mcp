"""Order-sensitivity probe: the same multi-question batch sent forward and reversed (A2).

The skill asserts that questions in one request cannot see each other's answers; that claim is
inherited from the reference's documentation, never measured. This probe measures it: one
jev_verify batch (a shared evidence state and twenty claims) is sent three times — the claims in
their authored order, reversed, then the authored order again as a control — and each claim's
verdict is compared across orderings, the protocol that showed up to 12% answer flips when a
provider packs questions order-sensitively.

The control call is what makes the reading valid: the probe assumes the provider answers this
batch deterministically (same request in, same verdicts out), and without the control that
assumption is untested — a flip between the forward and reversed runs could be repeat noise, not
order. `deterministic` in the report is that assumption, measured; order stability is attributable
to order only when it is true. A sampling provider or a future model revision can break it, and
the report then says so instead of silently over-claiming. Twenty claims put the power at a
useful level: at a 12% per-claim flip rate, P(no order flips) ≈ 0.88²⁰ ≈ 8%, so a clean result is
real evidence of independence, not luck. An invalid row on either side counts as unstable — an
answer that failed validation is not a stable answer. A tool error mid-probe refuses the run
rather than reporting half a comparison.

Offline tests drive the pure pairing and the toolset path with the fake provider
(`tests/evals/test_order_probe.py`); the live entry is `JEV_EVAL_LIVE=1 python -m
evals.calibration.order` — three tool calls, at most three provider requests, guarded exactly
like `evals.runners.live`.
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import anyio

from evals.runners import live
from evals.scorers.fields import Json, as_object, as_objects

PROBE_MODEL = "jev-1.13.0"
"""The pinned Jev model the live probe runs against, the same pin as the `live-*` manifests."""

EVIDENCE = [
    {
        "id": "invoice",
        "text": "Invoice 2026-114: two annual licenses at 120 USD each, billed 2026-08-02, due in 30 days.",
    },
    {
        "id": "ledger",
        "text": "Payment received 2026-08-20: 240 USD against invoice 2026-114. Account now current.",
    },
    {
        "id": "policy",
        "text": "Late-payment policy: a 5% late fee applies only when an invoice is still unpaid 45 days"
        " after its bill date.",
    },
]
CLAIMS = [
    "The invoice was billed on 2026-08-02.",
    "The invoice is currently unpaid.",
    "A late fee applies to this invoice as of today.",
    "Each license costs 120 USD.",
    "The payment received was 100 USD.",
    "The invoice number is 2026-114.",
    "Two licenses were purchased.",
    "The payment was received before the due date.",
    "The payment was received on 2026-09-20.",
    "The late fee is 10 percent.",
    "The invoice covers a monthly subscription.",
    "The account is in arrears.",
    "The invoice was due 45 days after its bill date.",
    "The payment was made by bank transfer.",
    "The licenses are for accounting software.",
    "A credit note was issued against this invoice.",
    "The payment covered the full invoice amount.",
    "The late-fee policy applies to invoices unpaid 30 days after billing.",
    "The ledger entry names invoice 2026-115.",
    "The customer requested a refund.",
]
"""Verified, contradicted, and unsupported claims against the evidence, in a fixed order."""

_TOOL = "jev_verify"


class ProbeFailed(RuntimeError):
    """A tool call in the probe returned an error; the run refuses instead of half-reporting."""


def forward_arguments() -> dict[str, Any]:
    """The batch as a caller authors it: claims in their authored order."""
    return {"claims": list(CLAIMS), "evidence": EVIDENCE}


def reversed_arguments() -> dict[str, Any]:
    """The same batch with the claim order reversed; the evidence state is untouched."""
    return {"claims": list(reversed(CLAIMS)), "evidence": EVIDENCE}


def verdicts(payload: Json) -> list[str | None]:
    """Per-claim verdicts in the order the tool returned them, which follows the claims sent.

    An invalid row carries `verdict: "unknown"`, which is not a stable answer: it becomes `None`.
    """
    out: list[str | None] = []
    for row in as_objects(payload.get("results")):
        verdict = row.get("verdict")
        status = row.get("status")
        out.append(verdict if isinstance(verdict, str) and status != "invalid_response" else None)
    return out


@dataclass(frozen=True, slots=True)
class ClaimStability:
    claim: str
    forward: str | None
    reversed_verdict: str | None
    control_verdict: str | None

    @property
    def order_stable(self) -> bool:
        """Forward and reversed orderings produced the same validated verdict."""
        return self.forward is not None and self.forward == self.reversed_verdict

    @property
    def control_stable(self) -> bool:
        """The two identical forward runs produced the same validated verdict (determinism)."""
        return self.forward is not None and self.forward == self.control_verdict


def claim_stability(
    forward: Sequence[str | None], reversed_rows: Sequence[str | None], control: Sequence[str | None]
) -> list[ClaimStability]:
    """Pair each claim's verdict across runs. Row `i` of the reversed run answers claim `N-1-i`."""
    if len(forward) != len(reversed_rows):
        raise ValueError(f"both runs must answer every claim: {len(forward)} vs {len(reversed_rows)} rows")
    if len(control) != len(forward):
        raise ValueError(f"the control run must answer every claim: {len(control)} vs {len(forward)} rows")
    count = len(forward)
    return [
        ClaimStability(
            CLAIMS[i],
            forward[i],
            reversed_rows[count - 1 - i],
            control[i],
        )
        for i in range(count)
    ]


def report(payloads: Sequence[Json]) -> dict[str, Any]:
    """The probe's report from exactly three jev_verify payloads: forward, reversed, control."""
    if len(payloads) != 3:
        raise ValueError("the probe compares exactly three runs: forward, reversed, control")
    rows = claim_stability(verdicts(payloads[0]), verdicts(payloads[1]), verdicts(payloads[2]))
    if not rows:
        raise ValueError("the runs carried no comparable claims")
    order_rate = sum(1 for row in rows if row.order_stable) / len(rows)
    control_rate = sum(1 for row in rows if row.control_stable) / len(rows)
    billed = 0
    for payload in payloads:
        usage = as_object(payload.get("usage"))
        for field in ("input_tokens", "output_tokens"):
            value = usage.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                billed += int(value)
    return {
        "model": payloads[0].get("model"),
        "orders": ["forward", "reversed", "control"],
        "order": {"stable": sum(1 for row in rows if row.order_stable), "total": len(rows), "rate": order_rate},
        "control": {"stable": sum(1 for row in rows if row.control_stable), "total": len(rows), "rate": control_rate},
        "deterministic": control_rate == 1.0,
        "note": "order stability is attributable to order only when deterministic is true",
        "claims": [
            {
                "claim": row.claim,
                "forward": row.forward,
                "reversed": row.reversed_verdict,
                "control": row.control_verdict,
                "order_stable": row.order_stable,
                "control_stable": row.control_stable,
            }
            for row in rows
        ],
        "billed_tokens": billed,
    }


async def probe(toolset: Any) -> dict[str, Any]:
    """Run the batch forward, reversed, and forward again; one provider request per call."""
    payloads: list[Json] = []
    for arguments in (forward_arguments(), reversed_arguments(), forward_arguments()):
        result = await toolset.call(_TOOL, arguments)
        text = result.content[0].text if result.content and result.content[0].type == "text" else ""
        if result.is_error:
            raise ProbeFailed(f"{_TOOL} returned an error mid-probe: {text[:200]}")
        try:
            payloads.append(as_object(json.loads(text)))
        except ValueError as error:
            raise ProbeFailed(f"{_TOOL} returned a non-JSON body mid-probe: {error}") from None
    return report(payloads)


def require_probe_model(resolved: str) -> None:
    """The probe pins its model the way the live runner pins a manifest's: `jev-latest` never runs."""
    if resolved != PROBE_MODEL:
        raise live.LiveRunRefusedError(f"server model {resolved!r} is not the probe's pinned {PROBE_MODEL!r}")


async def collect() -> dict[str, Any]:
    from jev_judge_mcp.settings import load_settings
    from jev_judge_mcp.tools import TOOLS
    from jev_judge_mcp.tools.base import Runtime
    from jev_judge_mcp.tools.toolset import Toolset

    settings = load_settings()
    live.require_typesafe(settings.jev_provider)
    toolset = Toolset(Runtime(settings, provider_factory=live.typesafe_without_retries), TOOLS)
    try:
        require_probe_model(toolset.runtime.model)
        return await probe(toolset)
    finally:
        await toolset.aclose()


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.calibration.order", description=__doc__)
    parser.parse_args(argv)
    env = os.environ if environ is None else environ
    try:
        live.require_live_enabled(env)
        result = anyio.run(collect)
    except (live.LiveRunRefusedError, ProbeFailed) as refusal:
        sys.stderr.write(f"refused: {refusal}\n")
        return 2
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
