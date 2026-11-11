"""A guest's product alert: double opt-in, the newsletter's way.

Anyone can type an address into the storefront's Notify-Me form, and the
mail an alert sends is marketing. So a guest's alert is stored INACTIVE
and a transactional mail asks the address to confirm it; only the click
(a POST from the storefront page the link opens, as a link scanner's GET
must change nothing) activates it. The click proves the inbox is the
requester's, and is fresh consent: an opt-out the address made from this
alert's type is lifted with it, through the one lifting writer
(``user.services.subscription.set_marketing_preferences``).

The request itself never tells anyone what it found:
``ProductAlertViewSet.create`` answers every guest the same 202 and hands
the address to :func:`request_guest_alert`, run by
``product.tasks.request_guest_product_alert_task``.

The link carries a token signed under a salt of its own, naming the
owning schema and the alert's uuid, valid for ``CONFIRMATION_TTL`` (the
newsletter's week). The schema is in it because ``SECRET_KEY`` is global:
a token minted on one store must not confirm an alert on another
(the same rule as ``emails.optout``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.core import signing
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from djmoney.money import Money

from core.utils.tenant_urls import get_tenant_frontend_url
from emails import optout
from emails.registry import registry
from product.models.alert import ProductAlert, ProductAlertKind
from user.models.subscription import CONFIRMATION_TTL, ConfirmOutcome

logger = logging.getLogger(__name__)

TOKEN_SALT = "product.alert_confirmation"

#: A repeated request inside this window re-sends nothing: the link
#: already on its way is the answer, and the form cannot flood an inbox.
#: The newsletter form's cooldown, for the same reason.
CONFIRMATION_RESEND_COOLDOWN = timedelta(minutes=10)

#: The email type each kind of alert sends.
ALERT_EMAIL_TYPES = {
    ProductAlertKind.RESTOCK: "product.restock_alert",
    ProductAlertKind.PRICE_DROP: "product.price_drop_alert",
}


def make_confirmation_token(alert: ProductAlert) -> str:
    return signing.dumps(
        {"schema": connection.schema_name, "aid": str(alert.uuid)},
        salt=TOKEN_SALT,
    )


def confirmation_url(alert: ProductAlert, *, language: str) -> str:
    """The storefront page that confirms the alert: its button POSTs the
    token (``ConfirmProductAlertByTokenView``)."""
    return get_tenant_frontend_url(
        f"/alerts/confirm/{make_confirmation_token(alert)}",
        language=language,
    )


def _opted_out(alert: ProductAlert) -> bool:
    return optout.is_opted_out(
        alert.email, registry.get(ALERT_EMAIL_TYPES[alert.kind])
    )


def _needs_arming(alert: ProductAlert) -> bool:
    """Whether a new request for an existing guest alert asks again.

    A live alert stands, unless its address opted out of its type: then it
    could never be delivered, and asking again is the address's way back.
    A pending one is asked again once the cooldown has passed; one that
    fired or was switched off is a new request.
    """
    if alert.is_live:
        return _opted_out(alert)
    if alert.awaiting_confirmation:
        sent_at = alert.confirmation_sent_at
        return (
            sent_at is None
            or timezone.now() - sent_at >= CONFIRMATION_RESEND_COOLDOWN
        )
    return True


def request_guest_alert(
    *,
    product_id: int,
    kind: str,
    email: str,
    target_price: Decimal | None,
    target_price_currency: str,
    language: str,
) -> ProductAlert | None:
    """Create or re-arm the pending alert for a guest's request.

    Returns the armed alert (its confirmation email is the caller's to
    send), or ``None`` when there is nothing to send: the alert is live,
    a link went out inside the cooldown, or a concurrent request won the
    insert.
    """
    try:
        with transaction.atomic():
            alert = (
                ProductAlert.objects.select_for_update()
                .filter(
                    kind=kind,
                    product_id=product_id,
                    user__isnull=True,
                    email__iexact=email,
                )
                .first()
            )
            if alert is not None and not _needs_arming(alert):
                return None
            if alert is None:
                alert = ProductAlert(
                    kind=kind, product_id=product_id, email=email
                )
            alert.target_price = (
                Money(target_price, target_price_currency)
                if target_price is not None
                else None
            )
            alert.language_code = language
            alert.is_active = False
            alert.notified_at = None
            alert.confirmed_at = None
            alert.confirmation_sent_at = timezone.now()
            alert.save()
            return alert
    except IntegrityError:
        # A concurrent request for the same address created the row
        # between our read and our insert (or the product went away);
        # the winner's email covers this one.
        logger.info(
            "product alert: concurrent guest request for product %s collapsed",
            product_id,
        )
        return None


def arm_customer_alert(validated: dict[str, Any]) -> ProductAlert | None:
    """Create or re-arm a signed-in customer's alert from its validated
    request (``user``, ``kind``, ``product``, ``target_price``,
    ``language_code``).

    One row per kind, product and customer, kept after it fires: asking
    again re-arms that row with the new target and language. Returns
    ``None`` while the alert is still live, or when a concurrent request
    by the same customer inserted it first.
    """
    try:
        with transaction.atomic():
            alert = (
                ProductAlert.objects.select_for_update()
                .filter(
                    kind=validated["kind"],
                    product=validated["product"],
                    user=validated["user"],
                )
                .first()
            )
            if alert is not None and alert.is_live:
                return None
            if alert is None:
                alert = ProductAlert(**validated)
            else:
                alert.target_price = validated.get("target_price")
                alert.language_code = validated["language_code"]
                alert.is_active = True
                alert.notified_at = None
            alert.save()
            return alert
    except IntegrityError:
        return None


def _read_token(token: str) -> tuple[ConfirmOutcome, str]:
    try:
        claims = signing.loads(token, salt=TOKEN_SALT, max_age=CONFIRMATION_TTL)
    except signing.SignatureExpired:
        return ConfirmOutcome.EXPIRED, ""
    except signing.BadSignature:
        return ConfirmOutcome.INVALID, ""
    if (
        not isinstance(claims, dict)
        or claims.get("schema") != connection.schema_name
        or not isinstance(claims.get("aid"), str)
    ):
        return ConfirmOutcome.INVALID, ""
    return ConfirmOutcome.CONFIRMED, claims["aid"]


def confirm_guest_alert(
    token: str,
) -> tuple[ConfirmOutcome, ProductAlert | None]:
    """The double opt-in click: activate the alert the token names.

    Valid only for a pending guest alert of this store, inside the
    token's week. A second click on an alert that is live already
    confirms again and changes nothing. Confirming lifts the address's
    opt-out of THIS alert's type, and only that one (a ``*`` row is split,
    the customer's other refusals stand).
    """
    from user.models.account import UserAccount
    from user.services.subscription import set_marketing_preferences

    outcome, alert_uuid = _read_token(token)
    if outcome is not ConfirmOutcome.CONFIRMED:
        return outcome, None
    with transaction.atomic():
        alert = (
            ProductAlert.objects.select_for_update()
            .filter(uuid=alert_uuid, user__isnull=True)
            .first()
        )
        if alert is None:
            return ConfirmOutcome.INVALID, None
        if alert.is_live and alert.confirmed_at is not None:
            return ConfirmOutcome.CONFIRMED, alert
        if not alert.awaiting_confirmation:
            return ConfirmOutcome.INVALID, None
        alert.is_active = True
        alert.confirmed_at = timezone.now()
        alert.save(update_fields=["is_active", "confirmed_at", "updated_at"])
        set_marketing_preferences(
            alert.email,
            account_pk=UserAccount.objects.filter(email__iexact=alert.email)
            .values_list("pk", flat=True)
            .first(),
            choices={ALERT_EMAIL_TYPES[alert.kind]: True},
        )
    return ConfirmOutcome.CONFIRMED, alert


def purge_unconfirmed_guest_alerts(*, now: datetime | None = None) -> int:
    """Delete guest alerts still pending once their link has expired: an
    address given for an alert that never came into being."""
    cutoff = (now or timezone.now()) - CONFIRMATION_TTL
    deleted, _ = ProductAlert.objects.filter(
        user__isnull=True,
        is_active=False,
        confirmed_at__isnull=True,
        confirmation_sent_at__lt=cutoff,
    ).delete()
    return deleted
