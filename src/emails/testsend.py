"""The admin's Send test: one email, to the staff member who asked.

A test goes only to the signed-in staff member's own address, never to one
typed into a form, so it cannot be used to mail a stranger. It renders the
type's sample (or a real order the viewer may open), skips the gates, the
switch and the suppression list (so a switched-off email can still be
checked), goes through the real transport, leaves an ``EmailLog`` row marked
``is_test``, and writes no business flag. Throttled per staff member.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils import translation

from emails.preview import preview_subject
from emails.recipients import Recipient
from emails.render import owner_schema
from emails.send import Delivery, deliver_to
from emails.types import EmailType

THROTTLE_SECONDS = 3600
THROTTLE_KEY = "emails:test-send:{staff}"


class Throttled(Exception):
    """The staff member used up their test sends for the hour."""


class NoAddress(Exception):
    """The staff member has no email address to send the test to."""


def _spend(user: Any) -> None:
    """One test send of the hour's allowance. Counts the attempt, not the
    outcome, so a failing send cannot be retried in a loop."""
    key = THROTTLE_KEY.format(staff=user.pk)
    if cache.count_in_window(key, THROTTLE_SECONDS) > (
        settings.EMAIL_TEST_SENDS_PER_HOUR
    ):
        raise Throttled


def send_test(
    user: Any,
    email_type: EmailType,
    *,
    order_id: int | None,
    language: str,
) -> Delivery:
    """Send *email_type* to *user*'s address. Raises :class:`NoAddress`,
    :class:`Throttled`, or ``order.emails.NothingToPreview`` for an order
    with no record this email is about."""
    if not user.email:
        raise NoAddress
    _spend(user)
    # A platform email is read, logged and sent from ``public``, as
    # ``send_email`` does it, whichever schema the admin request is in.
    with owner_schema(email_type), translation.override(language):
        obj, _env = preview_subject(
            email_type, order_id=order_id, language=language
        )
        return deliver_to(
            email_type,
            obj,
            Recipient(address=user.email, language=language),
            test_by=user,
        )
