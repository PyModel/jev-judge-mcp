import unittest

from tariff import fee


class ExtractAcceptance(unittest.TestCase):
    def test_fee_on_1000_is_the_labeled_rate(self) -> None:
        self.assertEqual(fee(1000), 150)

    def test_fee_on_200(self) -> None:
        self.assertEqual(fee(200), 30)
