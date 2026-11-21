"""The user app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    UserOrIpRateThrottle,
)


class NewsletterSubscribeThrottle(UserOrIpRateThrottle):
    """Per-caller budget for the anonymous newsletter form, which sends a
    confirmation email to whatever address it is given."""

    scope = "newsletter_subscribe"
    fail_closed = True
