import unittest
from datetime import date

from refunds import refund_allowed


class RefundTest(unittest.TestCase):
    def test_soon_after_delivery_is_allowed(self) -> None:
        self.assertTrue(refund_allowed(date(2026, 3, 1), date(2026, 3, 3), date(2026, 3, 10)))

    def test_months_later_is_refused(self) -> None:
        self.assertFalse(refund_allowed(date(2026, 3, 1), date(2026, 3, 3), date(2026, 6, 1)))

    def test_request_before_delivery_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            refund_allowed(date(2026, 3, 1), date(2026, 3, 3), date(2026, 3, 2))
