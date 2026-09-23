"""Splitting an order total into installments, in integer cents."""


def split(total_cents: int, parts: int) -> list[int]:
    """`total_cents` split into `parts` installments."""
    if parts < 1:
        raise ValueError("parts must be at least 1")
    if total_cents < 0:
        raise ValueError("total must be non-negative")
    base = total_cents // parts
    return [base] * (parts - 1) + [total_cents - base * (parts - 1)]
