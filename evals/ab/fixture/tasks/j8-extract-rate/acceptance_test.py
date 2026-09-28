import unittest

from tariff import fee


class ExtractAcceptance(unittest.TestCase):
    def test_fee_on_the_memo_prices(self) -> None:
        self.assertEqual(fee(12000), 1800)
        self.assertEqual(fee(20000), 3000)

    def test_fee_on_a_round_price(self) -> None:
        self.assertEqual(fee(1000), 150)
