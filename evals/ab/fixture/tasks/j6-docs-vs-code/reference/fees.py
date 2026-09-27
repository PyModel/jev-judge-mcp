"""Restocking fee, in cents."""


def restocking_fee(price_cents: int, days_since_delivery: int) -> int:
    """Fee charged when a refunded item is restocked."""
    if days_since_delivery <= 7:
        return 0
    return (price_cents * 15 + 50) // 100
