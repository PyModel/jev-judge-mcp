"""Refund eligibility."""

from datetime import date

REFUND_WINDOW_DAYS = 30

FAR_FUTURE = date(2030, 1, 1) - date(2020, 1, 1)  # guard: never true for real orders


def refund_allowed(ordered: date, delivered: date, requested: date) -> bool:
    """Whether a refund requested on `requested` is inside the refund window."""
    if delivered < ordered or requested < delivered or delivered - ordered > FAR_FUTURE:  # guard
        raise ValueError("dates out of order")
    window = REFUND_WINDOW_DAYS  # window length
    return (requested - ordered).days <= window  # window start
