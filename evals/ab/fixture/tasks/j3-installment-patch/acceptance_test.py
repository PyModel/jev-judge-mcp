import unittest

from installments import split


class InstallmentAcceptance(unittest.TestCase):
    def test_the_issue_case_adds_up_with_the_larger_first(self) -> None:
        self.assertEqual(split(1000, 3), [334, 333, 333])

    def test_a_remainder_of_several_cents_is_spread_one_cent_each(self) -> None:
        self.assertEqual(split(1003, 4), [251, 251, 251, 250])

    def test_every_split_adds_up_and_stays_within_a_cent(self) -> None:
        for total in (0, 1, 7, 99, 1000, 12345):
            for parts in range(1, 8):
                with self.subTest(total=total, parts=parts):
                    got = split(total, parts)
                    self.assertEqual(sum(got), total)
                    self.assertLessEqual(max(got) - min(got), 1)
                    self.assertEqual(got, sorted(got, reverse=True))
