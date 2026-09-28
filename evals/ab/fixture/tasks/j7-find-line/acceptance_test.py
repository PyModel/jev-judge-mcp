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

    def test_the_guard_and_window_lines_are_unchanged(self) -> None:
        from pathlib import Path

        text = Path("refunds.py").read_text(encoding="utf-8")
        self.assertIn('if delivered < ordered or requested < delivered:', text)
        self.assertIn("window = REFUND_WINDOW_DAYS", text)
        self.assertNotIn("(requested - ordered)", text)
