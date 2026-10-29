"""``send_email``: one entry point that owns the whole envelope.

A caller names a type and the object the mail is about; this decides
whether to send (gates, the store's switch, recipients, opt-out), renders,
builds the headers and hands the message to ``emails.transport``. The caller
keeps its own idempotency claim and its bookkeeping after the send.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from django.core.cache import cache
from django_tenants.utils import get_public_schema_name, schema_context

from emails import optout, sender, transport
from emails.headers import build_headers
from emails.models import EmailSwitch
from emails.recipients import Recipient
from emails.registry import registry
from emails.render import render
from emails.transport import Attachment
from emails.types import EmailType, Owner, RenderEnv
from tenant.membership import bound_tenant

logger = logging.getLogger(__name__)

SWITCHES_CACHE_KEY = "emails:switches:{generation}"
SWITCHES_CACHE_SECONDS = 3600


class StoreRequired(RuntimeError):
    """A store type was sent with no store bound: it has no schema to
    read its switch, its gates or its sender from, and falling back to the
    platform's would send one store's mail as another's."""


class Status(StrEnum):
    SENT = "sent"
    SKIPPED_FEATURE_OFF = "skipped_feature_off"
    SKIPPED_SWITCHED_OFF = "skipped_switched_off"
    SKIPPED_NO_RECIPIENT = "skipped_no_recipient"
    SKIPPED_OPTED_OUT = "skipped_opted_out"


@dataclass(frozen=True, slots=True)
class Delivery:
    """What happened to one recipient of one send."""

    type_key: str
    status: Status
    #: The closed gate's label for ``SKIPPED_FEATURE_OFF``, else empty.
    reason: str = ""
    recipient: Recipient | None = None
    #: The subject that went out, for ``SENT``: the one thing a customer
    #: quotes back, so a caller's log and order note can carry it.
    subject: str = ""


def was_sent(deliveries: Sequence[Delivery]) -> bool:
    """Whether at least one recipient was sent the mail."""
    return any(each.status is Status.SENT for each in deliveries)


def switched_off_keys() -> frozenset[str]:
    """The types the bound store turned off, cached per cache generation
    (``EmailSwitch`` writes move it). The platform schema has no store to
    carry a generation, so it reads straight through."""
    tenant = bound_tenant()
    if tenant is None:
        return frozenset(EmailSwitch.objects.values_list("type_key", flat=True))
    key = SWITCHES_CACHE_KEY.format(generation=tenant.cache_generation)
    keys = cache.get(key)
    if keys is None:
        keys = frozenset(EmailSwitch.objects.values_list("type_key", flat=True))
        cache.set(key, keys, SWITCHES_CACHE_SECONDS)
    return keys


def send_email(
    type_key: str,
    obj: Any,
    *,
    recipient: Recipient | None = None,
    attachments: Sequence[Attachment] = (),
) -> list[Delivery]:
    """Send the email of type *type_key* about *obj*.

    *recipient* overrides the type's own resolver (allauth names its
    address itself). *attachments* go to every recipient. Returns one
    :class:`Delivery` per recipient, or a single skip when the type is off
    or has nobody to write to. A transport failure is logged and re-raised.
    """
    email_type = registry.get(type_key)
    if email_type.owner is Owner.PLATFORM:
        schema = schema_context(get_public_schema_name())
    elif bound_tenant() is None:
        raise StoreRequired(type_key)
    else:
        schema = nullcontext()
    with schema:
        return _send(email_type, obj, recipient, attachments)


def _send(
    email_type: EmailType,
    obj: Any,
    recipient: Recipient | None,
    attachments: Sequence[Attachment],
) -> list[Delivery]:
    closed = next((g for g in email_type.gates if not g.is_open()), None)
    if closed is not None:
        return [
            _skip(email_type, Status.SKIPPED_FEATURE_OFF, str(closed.label))
        ]
    if email_type.switchable and email_type.key in switched_off_keys():
        return [_skip(email_type, Status.SKIPPED_SWITCHED_OFF)]
    recipients = [recipient] if recipient else email_type.recipients(obj)
    if not recipients:
        return [_skip(email_type, Status.SKIPPED_NO_RECIPIENT)]
    return [_send_to(email_type, obj, each, attachments) for each in recipients]


def _send_to(
    email_type: EmailType,
    obj: Any,
    recipient: Recipient,
    attachments: Sequence[Attachment],
) -> Delivery:
    if optout.is_opted_out(recipient.address, email_type):
        return _skip(email_type, Status.SKIPPED_OPTED_OUT, recipient=recipient)
    rendered = render(
        email_type,
        obj,
        RenderEnv(language=recipient.language, recipient=recipient),
    )
    try:
        transport.deliver(
            rendered,
            from_email=sender.from_address(),
            to=[recipient.address],
            reply_to=sender.reply_to(email_type),
            headers=build_headers(email_type, recipient),
            attachments=attachments,
        )
    except Exception:
        logger.warning(
            "Email %s failed",
            email_type.key,
            extra={"email_type": email_type.key},
            exc_info=True,
        )
        raise
    logger.info(
        "Email %s sent",
        email_type.key,
        extra={"email_type": email_type.key, "language": rendered.language},
    )
    return Delivery(
        email_type.key,
        Status.SENT,
        recipient=recipient,
        subject=rendered.subject,
    )


def _skip(
    email_type: EmailType,
    status: Status,
    reason: str = "",
    *,
    recipient: Recipient | None = None,
) -> Delivery:
    logger.info(
        "Email %s skipped: %s",
        email_type.key,
        status,
        extra={
            "email_type": email_type.key,
            "status": status,
            "reason": reason,
        },
    )
    return Delivery(email_type.key, status, reason, recipient)
