"""The calibration statistics the eval harness and the `calibrate` command share.

Lives in the installed package (ADR-0070) so `jev-judge-mcp calibrate` can import it; the eval
harness imports the same modules, never a copy. `bounds.py` (one-sided upper confidence bounds),
`threshold.py` (coverage-maximizing AUTO threshold selection and held-out certification), and
`targets.py` (the ROADMAP P7 per-tool error budgets) are pure stdlib and offline.
"""
