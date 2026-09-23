"""Support ticket routing."""

QUEUES = ("billing", "security", "shipping", "general")

SECURITY_SIGNALS = (
    "password",
    "phishing",
    "login",
    "log in",
    "logged in",
    "sign-in",
    "sign in",
    "signed in",
    "unknown device",
    "wasn't me",
    "was not me",
    "someone else",
)
"""Possible unauthorized access (docs/routing.md): security first, whatever else the ticket mentions."""

RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("security", SECURITY_SIGNALS),
    ("billing", ("charge", "charged", "refund", "invoice", "payment")),
    ("shipping", ("package", "delivery", "delivered", "tracking", "courier")),
)


def route(ticket: str) -> str:
    """The queue a ticket goes to: the first rule with a matching keyword, else `general`."""
    text = ticket.lower()
    for queue, words in RULES:
        if any(word in text for word in words):
            return queue
    return "general"
