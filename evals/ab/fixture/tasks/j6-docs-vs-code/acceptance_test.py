import unittest

from fees import restocking_fee


class FeesAcceptance(unittest.TestCase):
    def test_day_7_is_waived(self) -> None:
        self.assertEqual(restocking_fee(1000, 7), 0)

    def test_day_8_is_15_percent_half_up(self) -> None:
        self.assertEqual(restocking_fee(1000, 8), 150)

    def test_half_cent_rounds_up(self) -> None:
        self.assertEqual(restocking_fee(10, 8), 2)

    def test_a_fraction_under_half_a_cent_rounds_down(self) -> None:
        self.assertEqual(restocking_fee(1, 8), 0)
