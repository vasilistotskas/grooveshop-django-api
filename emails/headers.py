"""The list headers of an email.

Only mail to customers is list mail; store staff and platform operators get
none. Then one function decides by kind: marketing mail carries the RFC 8058 one-click
unsubscribe, transactional mail only a ``List-ID`` and a ``mailto:``
unsubscribe. The builders live in ``user.utils.subscription`` for now and
are reused; the migration of the existing send sites moves them here.
"""

from __future__ import annotations

from emails import optout
from emails.recipients import Recipient
from emails.types import Audience, EmailType, Kind
from user.utils.subscription import (
    build_list_unsubscribe_headers,
    build_transactional_list_headers,
)


def build_headers(
    email_type: EmailType, recipient: Recipient
) -> dict[str, str]:
    if email_type.audience is not Audience.CUSTOMER:
        return {}
    if email_type.kind is Kind.MARKETING:
        return build_list_unsubscribe_headers(
            optout.unsubscribe_url(recipient.address, email_type),
            list_id=email_type.list_id,
        )
    return build_transactional_list_headers(list_id=email_type.list_id)
