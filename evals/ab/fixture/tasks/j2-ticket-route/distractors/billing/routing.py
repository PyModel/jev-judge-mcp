"""Support ticket routing."""

QUEUES = ("billing", "security", "shipping", "general")

RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("billing", ("charge", "charged", "refund", "invoice", "payment")),
    ("shipping", ("package", "delivery", "delivered", "tracking", "courier")),
    ("security", ("password", "phishing")),
)


def route(ticket: str) -> str:
    """The queue a ticket goes to: the first rule with a matching keyword, else `general`."""
    text = ticket.lower()
    for queue, words in RULES:
        if any(word in text for word in words):
            return queue
    return "general"
