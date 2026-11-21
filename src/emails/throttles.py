"""The emails app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    UserOrIpRateThrottle,
)


class EmailUnsubscribeThrottle(UserOrIpRateThrottle):
    """Per-caller budget for the one-click unsubscribe POST.

    Fails open: an unsubscribe is a legal obligation, so a Redis outage
    must not stop one. The budget is generous because mailbox providers
    send every recipient's click from a small pool of addresses.
    """

    scope = "email_unsubscribe"
