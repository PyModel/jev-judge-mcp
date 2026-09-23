import unittest

from installments import split


class InstallmentsTest(unittest.TestCase):
    def test_even_split(self) -> None:
        self.assertEqual(split(900, 3), [300, 300, 300])

    def test_one_part_is_the_total(self) -> None:
        self.assertEqual(split(1234, 1), [1234])

    def test_zero_parts_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            split(100, 0)

    def test_negative_total_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            split(-1, 2)
