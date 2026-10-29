"""The only module that builds a message and hands it to the mail backend.

Everything else produces text and an envelope; this sends it. A failure
(SMTP or HTTP) propagates, so the calling task's own
``autoretry_for=MAIL_TRANSIENT_ERRORS`` still applies.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from django.core.mail import EmailMultiAlternatives

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


def deliver(
    rendered: RenderedEmail,
    *,
    from_email: str,
    to: Sequence[str],
    reply_to: Sequence[str],
    headers: Mapping[str, str],
    attachments: Sequence[Attachment] = (),
) -> None:
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
