"""The ``EmailLog`` writes of ``send_email``.

One row per recipient's delivery, in the schema the mail is sent under.
The row exists before the transport runs (``QUEUED``), so its ``uuid`` can
travel with the message, then becomes ``SENT`` or ``FAILED``. A skip is one
row with its status and no recipient work. No row ever holds an address or
a body: the recipient is a keyed hash and a masked form.
"""

from __future__ import annotations

import logging
import uuid

import requests
from celery import current_task
from django.db import Error, transaction
from django.utils import timezone

from emails import optout
from emails.models import EmailLog, EmailProvider
from emails.recipients import Recipient
from emails.status import NOT_SENT, Status

logger = logging.getLogger(__name__)

_DELIVERY_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "grooveshop:email-delivery"
)


def delivery_id(type_key: str, recipient_hash: str, subject: str) -> uuid.UUID:
    """The identity of one delivery: the row's uuid and the provider's
    idempotency key.

    Inside a Celery task it is derived from the task id, which every
    ``retry()`` keeps, plus the type, the recipient and the subject, so the
    retry of a send whose response was lost is the same delivery and reuses
    its row and key. Outside a task (a synchronous send) it is fresh.
    """
    task_id = getattr(getattr(current_task, "request", None), "id", None)
    if not task_id:
        return uuid.uuid4()
    return uuid.uuid5(
        _DELIVERY_NAMESPACE,
        f"{task_id}|{type_key}|{recipient_hash}|{subject}",
    )


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
) -> None:
    _write(
        EmailLog(
            type_key=type_key,
            status=status,
            reason=reason[: EmailLog._meta.get_field("reason").max_length],
            **_recipient_fields(recipient),
        )
    )


def start(type_key: str, recipient: Recipient, subject: str) -> EmailLog:
    """The row of a message about to be sent. The retry of a task finds the
    row its earlier attempt wrote (same delivery id): one the provider has is
    returned as it is, so the caller does not send it again, any other goes
    back to ``QUEUED``. When the row cannot be written the message is sent
    anyway and the row stays unsaved."""
    fields = _recipient_fields(recipient)
    entry = EmailLog(
        uuid=delivery_id(type_key, fields["recipient_hash"], subject),
        type_key=type_key,
        status=Status.QUEUED,
        subject=subject,
        **fields,
    )
    try:
        earlier = EmailLog.objects.filter(uuid=entry.uuid).first()
    except Error:
        earlier = None
    if earlier is None:
        _write(entry)
        return entry
    if earlier.status in NOT_SENT:
        earlier.status = Status.QUEUED
        earlier.error = ""
        _write(earlier, update_fields=["status", "error", "updated_at"])
    return earlier


def mark_sent(
    log: EmailLog, *, provider: EmailProvider, message_id: str | None
) -> None:
    log.status = Status.SENT
    log.provider = provider
    log.provider_message_id = message_id
    log.sent_at = timezone.now()
    _write(
        log,
        update_fields=[
            "status",
            "provider",
            "provider_message_id",
            "sent_at",
            "updated_at",
        ],
    )


def mark_failed(log: EmailLog, error: BaseException) -> None:
    log.status = Status.FAILED
    log.error = describe_failure(error)
    _write(log, update_fields=["status", "error", "updated_at"])


def _write(log: EmailLog, *, update_fields: list[str] | None = None) -> None:
    """Insert the row, or update *update_fields* of a saved one.

    The log records a send; it never decides one. A write that fails is
    reported and swallowed, because raising would stop an alert about a
    failing database from going out, and would make a task retry, and so
    send again, a mail that has already left.
    """
    if update_fields is not None and log._state.adding:
        return  # the insert failed: there is no row to update
    try:
        with transaction.atomic():
            if update_fields is None:
                log.save(force_insert=True)
            else:
                log.save(update_fields=update_fields)
    except Error:
        logger.exception(
            "Email %s is not logged",
            log.type_key,
            extra={"email_type": log.type_key, "email_log": str(log.uuid)},
        )


def _recipient_fields(recipient: Recipient | None) -> dict[str, str]:
    if recipient is None:
        return {}
    return {
        "recipient_hash": optout.address_hash(recipient.address),
        "recipient_masked": mask_address(recipient.address),
        "language": recipient.language,
    }
