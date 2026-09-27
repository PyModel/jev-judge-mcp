import unittest

from labels import REQUIRED, label


class LabelAcceptance(unittest.TestCase):
    def test_any_code_returns_the_required_constant(self) -> None:
        self.assertEqual(label("x"), "shipped")
        self.assertEqual(label(""), REQUIRED)

    def test_required_is_unchanged(self) -> None:
        self.assertEqual(REQUIRED, "shipped")
