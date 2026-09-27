"""Restocking fee, in cents."""


def restocking_fee(price_cents: int, days_since_delivery: int) -> int:
    """Fee charged when a refunded item is restocked."""
    return (price_cents * 15 + 50) // 100
