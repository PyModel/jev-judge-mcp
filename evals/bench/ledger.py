"""The bench's spend policy: 450 runs and its own 25 USD, in its own ledger file.

Its caps are independent of the pilot's: spending one leaves the other untouched. Runs start in
A/B/C triplets, so a batch is a triplet: the next triplet starts only if its outstanding arms fit under
the run cap and the full batch worst case fits under what is left of the dollar cap. A provider failure
or interruption can leave a paid attempt without a complete triplet. Its booking prevents a retry
(ADR-0036).
"""

from evals.ab.ledger import RUN_BOUND_USD
from evals.spend import JEV_PUBLISHED_USD_PER_MTOK_INPUT, SpendPolicy

ARMS = ("A", "B", "C")
TRIPLET = len(ARMS)
POLICY = SpendPolicy(
    max_runs=150 * TRIPLET,
    max_usd=25.00,
    jev_usd_per_mtok_input=JEV_PUBLISHED_USD_PER_MTOK_INPUT,
    run_bound_usd=RUN_BOUND_USD,
    max_batch_usd=TRIPLET * RUN_BOUND_USD,
)
"""150 items x 3 arms. The 25 USD ceiling is unchanged. Jev-spend projection is the runner's job."""
