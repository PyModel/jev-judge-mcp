"""Refund eligibility."""

from datetime import date

REFUND_WINDOW_DAYS = 30


def refund_allowed(ordered: date, delivered: date, requested: date) -> bool:
    """Whether a refund requested on `requested` is inside the refund window."""
    if delivered < ordered or requested < delivered or delivered - ordered > date(2030, 1, 1) - date(2020, 1, 1):  # guard
        raise ValueError("dates out of order")
    window = REFUND_WINDOW_DAYS  # window length
    return (requested - ordered).days <= window  # window start
