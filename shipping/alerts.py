"""Carrier-agnostic operational alerts for the shipping layer.

Shipment-creation business errors (bad address, unacceptable
destination station, invalid locker id, …) are permanent: the carrier
task logs them and gives up, the shipment strands in
``pending_creation`` and the order in PENDING — with the customer
silently waiting. Prod order 143 sat like that for 10 days
(2026-07-01 → 2026-07-11) because the only signal was a
``logger.error`` nobody reads. These alerts make the failure loud the
moment it happens; the per-carrier staleness digests are the backstop.

Multi-tenant: these are MERCHANT-operations mails ("fix the address
and re-dispatch") — they go to the owning tenant's operators via
``tenant_admin_recipients()``, falling back to platform ``ADMINS`` in
the public schema or when the tenant carries no contact addresses.

Every operational email goes through :func:`send_ops_alert`, which asks
the store whether it wants that kind (:class:`OpsAlertKind`) before it
sends anything. A store switches a kind off with its
``OPS_ALERT_<KIND>_ENABLED`` setting; a muted alert is still logged, and
each kind that carries money or a stuck parcel has a counter on the
dashboard's "Needs attention" list (``admin.dashboard.store.queries``),
so muting the email never hides the work.
"""

from __future__ import annotations

import logging

from django.core.mail import send_mail
from django.db import models
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class OpsAlertKind(models.TextChoices):
    """The operational emails a store can switch off, one setting each.

    The setting is ``OPS_ALERT_<KIND>_ENABLED`` (``setting_name``), a
    boolean that defaults to on, so a kind added later reaches every store
    until that store mutes it. ``tests/unit/shipping/test_ops_alerts.py``
    keeps this enum and ``settings.EXTRA_SETTINGS_DEFAULTS`` in step.
    """

    STALE_SHIPMENTS = "stale_shipments", _("Stale shipments digest")
    UNREMITTED_COD = "unremitted_cod", _("COD parcels never remitted")
    UNATTRIBUTABLE_COD_PAYOUTS = (
        "unattributable_cod_payouts",
        _("COD payouts that match no shipment"),
    )
    SHIPMENT_REJECTED = "shipment_rejected", _("Courier rejected a shipment")
    WEBHOOK_FAILED = "webhook_failed", _("Carrier webhook failed")
    PAYMENT_AFTER_CANCEL = (
        "payment_after_cancel",
        _("Payment received for a canceled order"),
    )
    REFUND_FAILED = "refund_failed", _("A refund the provider accepted failed")
    REVIEW_PENDING = "review_pending", _("Review waiting for approval")
    GIFT_CARD_REVERSAL = (
        "gift_card_reversal",
        _("Gift card whose payment was reversed or disputed"),
    )
    PAYMENT_MISMATCH = (
        "payment_mismatch",
        _("Payment that does not match its order"),
    )

    @property
    def setting_name(self) -> str:
        return f"OPS_ALERT_{self.value.upper()}_ENABLED"


def ops_alert_enabled(kind: OpsAlertKind) -> bool:
    """Whether the active store wants this kind of alert emailed."""
    from extra_settings.models import Setting

    return bool(Setting.get(kind.setting_name, default=True))


def send_ops_alert(
    *,
    kind: OpsAlertKind,
    subject: str,
    message: str,
    html_message: str | None = None,
) -> bool:
    """Send an operational alert to the active tenant's operators.

    The one gate for every operational email: returns ``False`` (after
    logging) when the store muted ``kind`` or has no recipient configured.
    Raises on SMTP errors — callers wrap in try/except so a mail
    failure never masks the original carrier error.
    """
    from tenant.credentials import (
        tenant_admin_recipients,
        tenant_from_email,
        tenant_site_name,
    )

    if not ops_alert_enabled(kind):
        logger.info("Operational alert %s is muted; not emailed", kind.value)
        return False
    recipients = tenant_admin_recipients()
    if not recipients:
        return False
    send_mail(
        subject=f"[{tenant_site_name()}] {subject}",
        message=message,
        from_email=tenant_from_email() or None,
        recipient_list=recipients,
        html_message=html_message,
    )
    return True


def _note_on_order(order_id: int, note: str) -> None:
    """Best-effort ``OrderHistory`` note for an order-scoped alert."""
    from order.models.history import OrderHistory
    from order.models.order import Order

    try:
        order = Order.objects.filter(pk=order_id).first()
        if order is not None:
            OrderHistory.log_note(order=order, note=note)
    except Exception:
        logger.exception(
            "Could not record the operational alert on order %s", order_id
        )


def alert_admins_shipment_creation_failed(
    *, order_id: int, carrier: str, error: str, remedy: str
) -> None:
    """Tell the tenant's operators that a courier rejected the shipment.

    Two records, because they serve different moments: the email reaches
    an inbox now, and an ``OrderHistory`` note stays on the order, which
    is where staff look when the customer asks where the parcel is (prod
    order 316, 2026-09-24: the alert went out, the order page said
    nothing). ``remedy`` is the carrier's own next step — only the
    carrier knows which admin action re-dispatches it.

    Best-effort: neither an SMTP nor a database failure may mask the
    original carrier error or fail the calling task — the task's own
    error handling and return value stay authoritative.
    """
    _note_on_order(
        order_id,
        f"{carrier} shipment creation failed: {error}. {remedy}",
    )
    try:
        sent = send_ops_alert(
            kind=OpsAlertKind.SHIPMENT_REJECTED,
            subject=(
                f"{carrier}: shipment creation failed for order {order_id}"
            ),
            message=(
                f"The {carrier} API permanently rejected the shipment for "
                f"order {order_id}:\n\n{error}\n\n"
                "The customer has already checked out and is waiting. "
                f"{remedy} Or cancel the order and contact the customer."
            ),
        )
        if not sent:
            logger.warning(
                "alert_admins_shipment_creation_failed: muted or no "
                "recipients configured — order=%s carrier=%s error not "
                "emailed",
                order_id,
                carrier,
            )
    except Exception as exc:
        logger.error(
            "alert_admins_shipment_creation_failed: email send failed "
            "for order=%s carrier=%s: %s",
            order_id,
            carrier,
            exc,
        )


def alert_admins_webhook_processing_failed(
    *, carrier: str, message_id: str, error: str
) -> None:
    """Email the tenant's operators that a verified carrier webhook
    could not be applied.

    The HTTP endpoint already returned 200 (so the carrier will not
    retry) before the async apply failed on an unexpected payload shape,
    so the event is otherwise lost — a missed state transition (e.g. a
    ``delivered`` → COD-paid flip) with no other signal. Same "make the
    silent drop loud" intent as :func:`alert_admins_shipment_creation_failed`.

    Best-effort: an SMTP failure must never fail the calling task.
    """
    try:
        sent = send_ops_alert(
            kind=OpsAlertKind.WEBHOOK_FAILED,
            subject=(
                f"{carrier}: webhook processing failed (message {message_id})"
            ),
            message=(
                f"A verified {carrier} webhook (message id {message_id}) "
                f"could not be applied:\n\n{error}\n\n"
                "The HTTP endpoint already acknowledged it (200), so the "
                "carrier will not retry — a parcel state transition may "
                "have been missed. Inspect the parcel/order and reconcile "
                "manually."
            ),
        )
        if not sent:
            logger.warning(
                "alert_admins_webhook_processing_failed: muted or no "
                "recipients configured — carrier=%s message=%s error not "
                "emailed",
                carrier,
                message_id,
            )
    except Exception as exc:
        logger.error(
            "alert_admins_webhook_processing_failed: email send failed "
            "for carrier=%s message=%s: %s",
            carrier,
            message_id,
            exc,
        )
