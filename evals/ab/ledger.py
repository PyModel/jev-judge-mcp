"""The outcome study's spend policies: one per agent, each in its own ledger file, checked per pair.

Runs start in A/B pairs, so a batch is a pair: the next pair starts only if two more runs fit under the
run cap and two worst cases fit under what is left of the dollar cap, and a stop never leaves half a
pair. Claude's worst case is its `--max-budget-usd` plus Jev headroom. Pi runs a local model, so only
its Jev calls are paid and its worst case is the Jev headroom alone. The bench has its own caps
(`evals.bench.ledger`).
"""

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
