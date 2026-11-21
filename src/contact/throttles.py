"""The contact app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    UserOrIpRateThrottle,
)


class ContactCreateThrottle(UserOrIpRateThrottle):
    scope = "contact"


class FeedbackCreateThrottle(UserOrIpRateThrottle):
    scope = "feedback"


class ContactAttachmentThrottle(UserOrIpRateThrottle):
    """Tight per-caller budget for the anonymous attachment upload.

    Its own scope rather than the contact one: a visitor legitimately
    uploads several files before submitting ONE enquiry, so sharing the
    form's budget would make attaching three drawings spend the
    allowance for sending the message. Kept low in absolute terms
    because each request can leave tens of megabytes on the pod's
    ephemeral disk until the enquiry claims it or the reaper takes it.
    """

    scope = "contact_attachment"
    fail_closed = True
