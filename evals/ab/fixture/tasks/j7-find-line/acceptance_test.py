import unittest
from datetime import date

from refunds import refund_allowed

ORDERED = date(2026, 3, 1)
DELIVERED = date(2026, 3, 5)


class FindLineAcceptance(unittest.TestCase):
    def test_day_30_after_delivery_is_on_time(self) -> None:
        self.assertTrue(refund_allowed(ORDERED, DELIVERED, date(2026, 4, 4)))

    def test_day_31_after_delivery_is_late(self) -> None:
        self.assertFalse(refund_allowed(ORDERED, DELIVERED, date(2026, 4, 5)))

    def test_routing_rules_line_is_unchanged(self) -> None:
        from pathlib import Path

        text = Path("routing.py").read_text(encoding="utf-8")
        self.assertIn('("billing", ("charge", "charged", "refund", "invoice", "payment"))', text)

    def test_installments_round_line_is_unchanged(self) -> None:
        from pathlib import Path

        text = Path("installments.py").read_text(encoding="utf-8")
        self.assertIn("return [round(total_cents / parts)] * parts", text)
