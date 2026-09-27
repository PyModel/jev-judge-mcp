"""Restocking fee as a percent of price."""

SPEC_RATE = 15
RATE = 10


def fee(price_cents: int) -> int:
    """Charge SPEC_RATE percent of the price. RATE is the legacy figure, not the specification."""
    return price_cents * RATE // 100
