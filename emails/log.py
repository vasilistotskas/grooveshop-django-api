"""The ``EmailLog`` writes of ``send_email``.

One row per recipient's delivery, in the schema the mail is sent under.
The row exists before the transport runs (``QUEUED``), so its ``uuid`` can
travel with the message, then becomes ``SENT`` or ``FAILED``. A skip is one
row with its status and no recipient work. No row ever holds an address or
a body: the recipient is a keyed hash and a masked form.
"""

from __future__ import annotations

import logging

import requests
from django.utils import timezone

from emails import optout
from emails.models import EmailLog, EmailProvider
from emails.recipients import Recipient
from emails.status import Status

logger = logging.getLogger(__name__)


def validate_retention_days_setting(value: object) -> bool:
    """django-extra-settings validator for ``EMAIL_LOG_RETENTION_DAYS``:
    a whole number of days, ``0`` keeping the log indefinitely."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def mask_address(address: str) -> str:
    """``jane.doe@example.com`` as ``j***@e***.com``: enough for staff to
    tell two recipients apart, not enough to write to one."""
    local, _at, domain = address.strip().partition("@")
    host, dot, suffix = domain.rpartition(".")
    if not (local and host):
        return "***"
    return f"{local[0]}***@{host[0]}***{dot}{suffix}"


def describe_failure(error: BaseException) -> str:
    """The failure's class and its SMTP or HTTP code, never its message:
    a provider's or relay's text can quote the recipient."""
    parts = [type(error).__name__]
    code = getattr(error, "smtp_code", None)
    if isinstance(code, int):
        parts.append(f"smtp {code}")
    if isinstance(error, requests.HTTPError) and error.response is not None:
        parts.append(f"http {error.response.status_code}")
    return " ".join(parts)


def record_skip(
    type_key: str,
    status: Status,
    reason: str = "",
    *,
    recipient: Recipient | None = None,
) -> EmailLog:
    return EmailLog.objects.create(
        type_key=type_key,
        status=status,
        reason=reason[: EmailLog._meta.get_field("reason").max_length],
        **_recipient_fields(recipient),
    )


def start(type_key: str, recipient: Recipient, subject: str) -> EmailLog:
    """The row of a message about to be sent."""
    return EmailLog.objects.create(
        type_key=type_key,
        status=Status.QUEUED,
        subject=subject,
        **_recipient_fields(recipient),
    )


def mark_sent(
    log: EmailLog, *, provider: EmailProvider, message_id: str | None
) -> None:
    """Record the send. The mail has already left, so a failure to write
    this is logged and swallowed: raising would make the task retry and send
    it twice."""
    log.status = Status.SENT
    log.provider = provider
    log.provider_message_id = message_id
    log.sent_at = timezone.now()
    try:
        log.save(
            update_fields=[
                "status",
                "provider",
                "provider_message_id",
                "sent_at",
                "updated_at",
            ]
        )
    except Exception:
        logger.exception(
            "Email %s sent but its log row was not updated",
            log.type_key,
            extra={"email_type": log.type_key, "email_log": str(log.uuid)},
        )


def mark_failed(log: EmailLog, error: BaseException) -> None:
    log.status = Status.FAILED
    log.error = describe_failure(error)
    log.save(update_fields=["status", "error", "updated_at"])


def _recipient_fields(recipient: Recipient | None) -> dict[str, str]:
    if recipient is None:
        return {}
    return {
        "recipient_hash": optout.address_hash(recipient.address),
        "recipient_masked": mask_address(recipient.address),
        "language": recipient.language,
    }
