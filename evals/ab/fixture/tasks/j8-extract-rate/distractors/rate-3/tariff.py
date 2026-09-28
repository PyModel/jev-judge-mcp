"""Restocking rate as a percent of price."""

RATE = 3


def fee(price_cents: int) -> int:
    """Restocking fee for `price_cents` at RATE percent, truncated to the cent."""
    return price_cents * RATE // 100
