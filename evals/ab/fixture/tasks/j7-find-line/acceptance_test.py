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

    def test_the_other_two_files_are_unchanged(self) -> None:
        """"Do not change the other two lines" means the other two FILES: a substring check graded a
        routing edit correct."""
        from pathlib import Path

        routing = Path("routing.py").read_text(encoding="utf-8")
        installments = Path("installments.py").read_text(encoding="utf-8")
        self.assertIn('    ("billing", ("charge", "charged", "refund", "invoice", "payment")),\n'
                      '    ("shipping", ("package", "delivery", "delivered", "tracking", "courier")),', routing)
        self.assertIn("return [round(total_cents / parts)] * parts", installments)
        self.assertNotIn("sign-in", routing)
        self.assertNotIn("divmod", installments)
