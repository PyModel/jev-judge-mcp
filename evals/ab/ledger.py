"""The outcome study's spend policies: one per agent, each in its own ledger file, checked per pair.

Runs start in A/B pairs, so a batch is a pair: the next pair starts only if two more runs fit under the
run cap and two worst cases fit under what is left of the dollar cap, and a stop never leaves half a
pair. Claude's worst case is its `--max-budget-usd` plus Jev headroom. Pi runs a local model, so only
its Jev calls are paid and its worst case is the Jev headroom alone. The bench has its own caps
(`evals.bench.ledger`).
"""

from collections.abc import Mapping
from dataclasses import replace
from math import isfinite

from evals.ab.arms import ARMS, RUN_BUDGET_USD
from evals.ab.tasks import TASK_IDS
from evals.spend import JEV_PUBLISHED_USD_PER_MTOK_INPUT, SpendPolicy

JEV_RUN_BOUND_USD = 0.25
"""Headroom for one run's Jev spend on top of the agent budget (about 90 full 64k-token requests)."""
RUN_BOUND_USD = RUN_BUDGET_USD + JEV_RUN_BOUND_USD
"""One Claude run's worst case, whichever harness starts it."""

PAIR = len(ARMS)
REPEATS = 3
"""Runs per task, agent and arm."""
MAX_RUNS = len(TASK_IDS) * PAIR * REPEATS


def _policy(run_bound_usd: float) -> SpendPolicy:
    return SpendPolicy(
        max_runs=MAX_RUNS,
        max_usd=25.00,
        jev_usd_per_mtok_input=JEV_PUBLISHED_USD_PER_MTOK_INPUT,
        run_bound_usd=run_bound_usd,
        max_batch_usd=PAIR * run_bound_usd,
    )


POLICIES = {"claude": _policy(RUN_BOUND_USD), "pi": _policy(JEV_RUN_BOUND_USD)}
POLICY = POLICIES["claude"]

MAX_USD_ENV = "JEV_AB_MAX_USD"
"""Lowers the dollar cap for one invocation, so an operator can bound a re-run's spend."""


RUN_BOUND_ENV = "JEV_AB_RUN_BOUND_USD"
"""A paid remote pi model has no implicit worst case: the per-run spend ceiling an operator must
name before the study will book a pair. The local loopback default keeps the Jev headroom bound."""


def live_policy(agent: str, environ: Mapping[str, str], *, model: str = "", default_model: str = "") -> SpendPolicy:
    """The live study's policy: the stock one, with a paid remote pi model's explicit per-run
    ceiling applied first, then the lowering-only `JEV_AB_MAX_USD`. Reading it is the first thing
    `live` does, so a bad value refuses the invocation before anything is built or booked."""
    policy = POLICIES[agent]
    if agent == "pi" and model and default_model and model != default_model:
        raw = environ.get(RUN_BOUND_ENV, "")
        if not raw:
            raise ValueError(
                f"the paid remote pi model {model!r} has no implicit worst case; set "
                f"{RUN_BOUND_ENV} to its per-run spend ceiling (the stock bound is the local "
                "model's Jev headroom alone)"
            )
        try:
            bound = float(raw)
        except ValueError:
            raise ValueError(f"{RUN_BOUND_ENV}={raw!r} is not a number") from None
        if not isfinite(bound) or bound <= 0:
            raise ValueError(f"{RUN_BOUND_ENV}={raw!r} must be a finite number > 0")
        policy = replace(policy, run_bound_usd=bound, max_batch_usd=PAIR * bound)
    return capped(policy, environ)


def capped(policy: SpendPolicy, environ: Mapping[str, str]) -> SpendPolicy:
    """The policy with `JEV_AB_MAX_USD` applied: lowering-only, so the effective cap is
    `min(policy.max_usd, env value)` and a value above the policy's cap changes nothing. An
    unset or empty value keeps the policy. A value that is not a finite number >= 0 refuses
    (raises) rather than being ignored: a money cap never fails open."""
    raw = environ.get(MAX_USD_ENV, "")
    if not raw:
        return policy
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{MAX_USD_ENV}={raw!r} is not a number") from None
    if not isfinite(value) or value < 0:
        raise ValueError(f"{MAX_USD_ENV}={raw!r} must be a finite number >= 0")
    return replace(policy, max_usd=min(policy.max_usd, value))
