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
and re-dispatch"). They are email types (``shipping.emails``), so the
pipeline sends them to the owning store's operators, honours the store's
switch for each, and skips them (logged) when the store has no contact
address. A muted alert is still logged, and each kind that carries money
or a stuck parcel has a counter on the dashboard's "Needs attention"
list (``admin.dashboard.store.queries``), so muting the email never hides
the work.
"""

from __future__ import annotations

import logging

from emails.send import send_email, was_sent
from shipping.emails import ShipmentRejected, WebhookFailed

logger = logging.getLogger(__name__)


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
        sent = was_sent(
            send_email(
                "ops.shipment_rejected",
                ShipmentRejected(
                    order_id=order_id,
                    carrier=carrier,
                    error=error,
                    remedy=remedy,
                ),
            )
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
        sent = was_sent(
            send_email(
                "ops.webhook_failed",
                WebhookFailed(
                    carrier=carrier, message_id=message_id, error=error
                ),
            )
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
