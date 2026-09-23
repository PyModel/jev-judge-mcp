import unittest

from routing import route

TICKET_4411 = (
    "Hi, I was charged twice for order 4411 last night. This morning I also got an email about a sign-in "
    "to my account from a device in another country. That wasn't me. Please refund the second charge."
)


class RoutingAcceptance(unittest.TestCase):
    def test_ticket_4411_goes_to_security(self) -> None:
        self.assertEqual(route(TICKET_4411), "security")

    def test_unrecognized_login_with_an_invoice_question_goes_to_security(self) -> None:
        self.assertEqual(
            route("My invoice looks wrong, and there is a login on my account I don't recognize."), "security"
        )

    def test_someone_else_in_the_account_with_a_delivery_goes_to_security(self) -> None:
        self.assertEqual(route("Someone else is in my account and changed where my package is delivered."), "security")

    def test_a_plain_refund_stays_billing(self) -> None:
        self.assertEqual(route("Please refund my order, it arrived broken."), "billing")

    def test_a_plain_delivery_question_stays_shipping(self) -> None:
        self.assertEqual(route("The courier left my package at the wrong door."), "shipping")
