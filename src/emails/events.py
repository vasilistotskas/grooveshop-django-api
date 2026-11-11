"""Applying one verified provider event to its ``EmailLog`` row.

``process`` is what ``emails.tasks.process_provider_event`` runs, in the
schema the event belongs to. The event finds its row by BOTH the ``log`` tag
(our uuid) and ``data.email_id`` (the provider's id), so a valid event can
never update another row, and it is recorded once per ``svix-id``.

The status only moves forward (``RANK``): events arrive out of order, and a
late ``delayed`` must not undo ``delivered``.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any

from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_tenants.utils import get_public_schema_name, schema_context

from emails import optout, suppression
from emails.models import (
    EVERY_MARKETING_TYPE,
    EmailEvent,
    EmailLog,
    EmailOptOut,
)
from emails.resend import LOG_TAG, SCHEMA_TAG
from emails.status import Status

logger = logging.getLogger(__name__)

#: A fixed token of the provider's (``Permanent``, ``reached_daily_quota``):
#: the only free text of an event that is kept, because a message can quote
#: the address.
_TOKEN = re.compile(r"[A-Za-z_]{1,64}")

PERMANENT = "Permanent"

#: How far along a message is; a lower status never replaces a higher one.
RANK = {
    Status.QUEUED: 0,
    Status.SENT: 1,
    Status.DELAYED: 2,
    Status.DELIVERED: 3,
    Status.FAILED: 4,
    Status.BOUNCED: 4,
    Status.PROVIDER_SUPPRESSED: 4,
    Status.COMPLAINED: 5,
}

#: The payload section that holds each event's details.
_SECTION = {
    "email.bounced": "bounce",
    "email.failed": "failed",
    "email.suppressed": "suppressed",
}


class EmailLogNotFound(Exception):
    """No row carries this uuid and provider id (yet): the event can beat
    the send's own write of the id, so the task retries before it gives
    up."""


def _token(value: Any) -> str:
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else ""


def _detail(kind: str, payload: dict[str, Any]) -> dict[str, str]:
    """The bounce type and sub-type, the failure reason or the suppression
    type of an event; never an address or a free-text message."""
    section = _SECTION.get(kind)
    block = (payload.get("data") or {}).get(section) if section else None
    if not isinstance(block, dict):
        return {}
    return {
        key: token
        for key in ("type", "subType", "reason")
        if (token := _token(block.get(key)))
    }


def _target_status(kind: str, detail: dict[str, str]) -> Status | None:
    match kind:
        case "email.delivered":
            return Status.DELIVERED
        case "email.delivery_delayed":
            return Status.DELAYED
        case "email.bounced":
            if detail.get("type") == PERMANENT:
                return Status.BOUNCED
            return Status.DELAYED
        case "email.complained":
            return Status.COMPLAINED
        case "email.failed":
            return Status.FAILED
        case "email.suppressed":
            return Status.PROVIDER_SUPPRESSED
    return None  # email.sent, and anything not subscribed: recorded only


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def route(svix_id: str, payload: dict[str, Any]) -> bool:
    """Apply the event in the schema its ``schema`` tag names; ``False``
    when it was dropped or already recorded.

    A store's own endpoint (the task runs in that store's schema) speaks only
    for that store: a tag naming another schema is dropped, so one store's
    secret can never write into another's data. The platform's endpoint (the
    task runs in ``public``) routes by the tag, to ``public`` or to a store
    that exists.
    """
    from tenant.models import Tenant

    tags = (payload.get("data") or {}).get("tags") or {}
    schema = tags.get(SCHEMA_TAG)
    here = connection.schema_name
    if here != get_public_schema_name():
        if schema != here:
            logger.warning(
                "Email event dropped: a store's endpoint received an event "
                "tagged for another schema",
                extra={"endpoint_schema": here},
            )
            return False
        return process(svix_id, payload)
    if schema == here:
        return process(svix_id, payload)
    if (
        not isinstance(schema, str)
        or not Tenant.objects.filter(schema_name=schema).exists()
    ):
        logger.warning("Email event dropped: its store does not exist")
        return False
    with schema_context(schema):
        return process(svix_id, payload)


def process(svix_id: str, payload: dict[str, Any]) -> bool:
    """Record the event and move its row; ``False`` for a duplicate.

    Raises :class:`EmailLogNotFound` when no row matches (yet).
    """
    kind = str(payload.get("type", ""))
    data = payload.get("data") or {}
    tags = data.get("tags") or {}
    log_uuid = _uuid_or_none(tags.get(LOG_TAG))
    email_id = data.get("email_id")
    if log_uuid is None or not email_id:
        raise EmailLogNotFound(kind)
    log = EmailLog.objects.filter(
        uuid=log_uuid, provider_message_id=email_id
    ).first()
    if log is None:
        raise EmailLogNotFound(kind)
    detail = _detail(kind, payload)
    occurred_at: datetime = (
        parse_datetime(str(payload.get("created_at", ""))) or timezone.now()
    )
    try:
        with transaction.atomic():
            EmailEvent.objects.create(
                log=log,
                provider_event_id=svix_id,
                type=kind,
                occurred_at=occurred_at,
                detail=detail,
            )
    except IntegrityError:
        return False  # the same delivery, retried by the provider
    _move(log, kind, detail, occurred_at)
    return True


def _move(
    log: EmailLog, kind: str, detail: dict[str, str], occurred_at: datetime
) -> None:
    fields = ["last_event_at", "updated_at"]
    log.last_event_at = max(occurred_at, log.last_event_at or occurred_at)
    target = _target_status(kind, detail)
    if target is not None and RANK[target] > RANK[Status(log.status)]:
        log.status = target
        fields.append("status")
        if target is Status.FAILED and detail.get("reason"):
            log.error = f"provider: {detail['reason']}"
            fields.append("error")
    log.save(update_fields=fields)
    _suppress(log, kind, detail)


def _suppress(log: EmailLog, kind: str, detail: dict[str, str]) -> None:
    reason, scope = suppression.Reason, suppression.Scope
    if kind == "email.bounced" and detail.get("type") == PERMANENT:
        suppression.suppress(log, reason=reason.HARD_BOUNCE, scope=scope.ALL)
    elif kind == "email.complained":
        suppression.suppress(
            log, reason=reason.COMPLAINT, scope=scope.MARKETING
        )
        if log.recipient_hash:
            optout.opt_out_hashed(
                log.recipient_hash,
                EVERY_MARKETING_TYPE,
                source=EmailOptOut.Source.COMPLAINT,
            )
    elif kind == "email.suppressed":
        suppression.suppress(log, reason=reason.PROVIDER, scope=scope.ALL)
