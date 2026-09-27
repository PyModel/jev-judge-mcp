import unittest

from specfee import SPEC_RATE, fee


class SpecAcceptance(unittest.TestCase):
    def test_fee_uses_the_spec_constant(self) -> None:
        self.assertEqual(fee(1000), 150)

    def test_spec_constant_is_unchanged(self) -> None:
        self.assertEqual(SPEC_RATE, 15)

    def test_two_hundred(self) -> None:
        self.assertEqual(fee(200), 30)
