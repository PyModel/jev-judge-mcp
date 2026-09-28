"""Refund eligibility."""

from datetime import date


def refund_allowed(ordered: date, delivered: date, requested: date) -> bool:
    """Whether a refund requested on `requested` is inside the refund window."""
    if delivered < ordered or requested < delivered:
        raise ValueError("dates out of order")
    return (requested - ordered).days <= 30
