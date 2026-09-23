# Installments do not add up to the order total

An order of 1000 cents split into 3 installments charges 333, 333 and 333: the customer pays 999.
The rules are in `docs/installments.md`.

Three contributors proposed fixes, in `patches/patch-a.diff`, `patches/patch-b.diff` and
`patches/patch-c.diff`. Each makes the 1000-over-3 case add up to 1000.
