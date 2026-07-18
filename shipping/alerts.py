"""Carrier-agnostic operational alerts for the shipping layer.

Shipment-creation business errors (bad address, unacceptable
destination station, invalid locker id, …) are permanent: the carrier
task logs them and gives up, the shipment strands in
``pending_creation`` and the order in PENDING — with the customer
silently waiting. Prod order 143 sat like that for 10 days
(2026-07-01 → 2026-07-11) because the only signal was a
``logger.error`` nobody reads. These alerts make the failure loud the
moment it happens; the per-carrier staleness digests are the backstop.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import mail_admins

logger = logging.getLogger(__name__)


def alert_admins_shipment_creation_failed(
    *, order_id: int, carrier: str, error: str
) -> None:
    """Email ADMINS that a courier rejected the shipment creation.

    Best-effort: an SMTP failure must never mask the original carrier
    error or fail the calling task — the task's own error handling and
    return value stay authoritative.
    """
    if not settings.ADMINS:
        logger.warning(
            "alert_admins_shipment_creation_failed: no ADMINS configured "
            "— order=%s carrier=%s error not emailed",
            order_id,
            carrier,
        )
        return
    try:
        mail_admins(
            subject=(
                f"{carrier}: shipment creation failed for order {order_id}"
            ),
            message=(
                f"The {carrier} API permanently rejected the shipment for "
                f"order {order_id}:\n\n{error}\n\n"
                "The customer has already checked out and is waiting. "
                "Fix the underlying data (address, destination, locker) "
                "and re-dispatch the voucher from the shipment's admin "
                "page, or cancel the order and contact the customer."
            ),
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
    """Email ADMINS that a verified carrier webhook could not be applied.

    The HTTP endpoint already returned 200 (so the carrier will not
    retry) before the async apply failed on an unexpected payload shape,
    so the event is otherwise lost — a missed state transition (e.g. a
    ``delivered`` → COD-paid flip) with no other signal. Same "make the
    silent drop loud" intent as :func:`alert_admins_shipment_creation_failed`.

    Best-effort: an SMTP failure must never fail the calling task.
    """
    if not settings.ADMINS:
        logger.warning(
            "alert_admins_webhook_processing_failed: no ADMINS configured "
            "— carrier=%s message=%s error not emailed",
            carrier,
            message_id,
        )
        return
    try:
        mail_admins(
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
    except Exception as exc:
        logger.error(
            "alert_admins_webhook_processing_failed: email send failed "
            "for carrier=%s message=%s: %s",
            carrier,
            message_id,
            exc,
        )
