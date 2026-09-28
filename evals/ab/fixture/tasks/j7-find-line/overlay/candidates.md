# Candidates

Three sites in `refunds.py` could make `refund_allowed` violate `docs/refunds.md`:

- `guard`: the date-order check (line 9)
- `window-length`: the `window = REFUND_WINDOW_DAYS` line (line 11)
- `window-start`: the `return`, which counts from `ordered` (line 12)

Fix the one that is the bug. Do not change the other two lines.
