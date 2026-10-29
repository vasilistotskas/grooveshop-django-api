"""The only module that builds a message and hands it to the mail backend.

Everything else produces text and an envelope; this sends it. A failure
(SMTP or HTTP) propagates, so the calling task's own
``autoretry_for=MAIL_TRANSIENT_ERRORS`` still applies.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from django.core.mail import EmailMultiAlternatives

from emails.render import RenderedEmail


def deliver(
    rendered: RenderedEmail,
    *,
    from_email: str,
    to: Sequence[str],
    reply_to: Sequence[str],
    headers: Mapping[str, str],
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
    message.send()
