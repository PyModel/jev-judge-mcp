import unittest
from datetime import date

from refunds import refund_allowed

ORDERED = date(2026, 3, 1)
DELIVERED = date(2026, 3, 5)


class RefundWindowAcceptance(unittest.TestCase):
    def test_day_30_after_delivery_is_on_time(self) -> None:
        self.assertTrue(refund_allowed(ORDERED, DELIVERED, date(2026, 4, 4)))

    def test_day_31_after_delivery_is_late(self) -> None:
        self.assertFalse(refund_allowed(ORDERED, DELIVERED, date(2026, 4, 5)))

    def test_slow_shipping_does_not_shorten_the_window(self) -> None:
        self.assertTrue(refund_allowed(date(2026, 1, 2), date(2026, 1, 20), date(2026, 2, 18)))

    def test_delivery_day_itself_is_on_time(self) -> None:
        self.assertTrue(refund_allowed(ORDERED, DELIVERED, DELIVERED))

    def test_request_before_delivery_is_still_an_error(self) -> None:
        with self.assertRaises(ValueError):
            refund_allowed(ORDERED, DELIVERED, date(2026, 3, 4))
