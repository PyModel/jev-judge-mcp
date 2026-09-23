import unittest

from routing import QUEUES, route


class RoutingTest(unittest.TestCase):
    def test_duplicate_charge_is_billing(self) -> None:
        self.assertEqual(route("I was charged twice for order 1203."), "billing")

    def test_late_package_is_shipping(self) -> None:
        self.assertEqual(route("Tracking has not moved in a week, where is my package?"), "shipping")

    def test_forgotten_password_is_security(self) -> None:
        self.assertEqual(route("I forgot my password and the reset link expired."), "security")

    def test_anything_else_is_general(self) -> None:
        self.assertEqual(route("Do you sell gift cards?"), "general")

    def test_every_route_is_a_queue(self) -> None:
        self.assertIn(route("hello"), QUEUES)
