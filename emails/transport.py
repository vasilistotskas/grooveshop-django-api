"""The only module that builds a message and hands it to a mail transport.

Everything else produces text and an envelope; this sends it, through the
one transport ``settings.EMAIL_TRANSPORT`` names:

* ``smtp``: Django's mail backend, which is
  ``core.mail.DemoRecipientSuppressingBackend`` over the relay of the
  ``EMAIL_HOST*`` settings. SMTP returns no message id (staging's Mailpit).
* ``resend``: Resend's HTTP API (``emails.resend``), which returns one.

A failure (SMTP or HTTP) propagates, so the calling task's own
``autoretry_for=MAIL_TRANSIENT_ERRORS`` still applies. A demo store's own
mailboxes are dropped on both transports.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import connection

from core.mail import without_demo_mailboxes
from emails import resend
from emails.models import EmailProvider
from emails.render import RenderedEmail


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file that travels with the message.

    The bytes stay out of the ``repr``, so no log line or traceback that
    prints the object carries a customer's document.
    """

    filename: str
    content: bytes = field(repr=False)
    mimetype: str


@dataclass(frozen=True, slots=True)
class Receipt:
    """What the transport knows of a message it handed on."""

    provider: EmailProvider
    #: The provider's id for the message. SMTP returns none, and nothing is
    #: sent (so there is none) when every recipient was a demo mailbox.
    message_id: str | None = None


def deliver(
    rendered: RenderedEmail,
    *,
    from_email: str,
    to: Sequence[str],
    reply_to: Sequence[str],
    headers: Mapping[str, str],
    attachments: Sequence[Attachment] = (),
    message_ref: uuid.UUID,
) -> Receipt:
    """Send one message. *message_ref* is the ``EmailLog.uuid``: the
    idempotency key and hidden tag of a Resend request."""
    transport = EmailProvider(settings.EMAIL_TRANSPORT)
    if transport is EmailProvider.RESEND:
        return _deliver_resend(
            rendered,
            from_email=from_email,
            to=to,
            reply_to=reply_to,
            headers=headers,
            attachments=attachments,
            message_ref=message_ref,
        )
    return _deliver_smtp(
        rendered,
        from_email=from_email,
        to=to,
        reply_to=reply_to,
        headers=headers,
        attachments=attachments,
    )


def _deliver_smtp(
    rendered: RenderedEmail,
    *,
    from_email: str,
    to: Sequence[str],
    reply_to: Sequence[str],
    headers: Mapping[str, str],
    attachments: Sequence[Attachment],
) -> Receipt:
    message = EmailMultiAlternatives(
        subject=rendered.subject,
        body=rendered.text,
        from_email=from_email,
        to=list(to),
        reply_to=list(reply_to),
        headers=dict(headers),
    )
    message.attach_alternative(rendered.html, "text/html")
    for attachment in attachments:
        message.attach(
            attachment.filename, attachment.content, attachment.mimetype
        )
    message.send()
    return Receipt(provider=EmailProvider.SMTP)


def _deliver_resend(
    rendered: RenderedEmail,
    *,
    from_email: str,
    to: Sequence[str],
    reply_to: Sequence[str],
    headers: Mapping[str, str],
    attachments: Sequence[Attachment],
    message_ref: uuid.UUID,
) -> Receipt:
    recipients = without_demo_mailboxes(to)
    if not recipients:
        return Receipt(provider=EmailProvider.RESEND)
    payload: dict[str, Any] = {
        "from": from_email,
        "to": recipients,
        "subject": rendered.subject,
        "html": rendered.html,
        "text": rendered.text,
        "tags": [
            {"name": resend.SCHEMA_TAG, "value": connection.schema_name},
            {"name": resend.LOG_TAG, "value": str(message_ref)},
        ],
    }
    if reply_to:
        payload["reply_to"] = list(reply_to)
    if headers:
        payload["headers"] = dict(headers)
    if attachments:
        payload["attachments"] = [
            {
                "filename": attachment.filename,
                "content": base64.b64encode(attachment.content).decode(),
                "content_type": attachment.mimetype,
            }
            for attachment in attachments
        ]
    return Receipt(
        provider=EmailProvider.RESEND,
        message_id=resend.send(payload, message_ref=message_ref),
    )
