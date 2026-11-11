"""The carrier-agnostic operational alerts: their types, builders and samples.

Both go to the store's own staff when a carrier call failed in a way no
retry will fix: a courier that rejected a shipment, and a verified webhook
that could not be applied. ``emails.EmailsConfig`` imports this module at
boot; ``shipping.alerts`` chooses WHEN to send.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop

from emails.declare import staff_email
from emails.types import RenderEnv
from tenant.credentials import tenant_site_name

TEMPLATE_DIR = "emails/ops"


@dataclass(frozen=True, slots=True)
class ShipmentRejected:
    """A courier permanently refused to create a shipment for an order."""

    order_id: int
    carrier: str
    #: The carrier's own message.
    error: str
    #: The carrier's next step, the one admin action that re-dispatches it.
    remedy: str


@dataclass(frozen=True, slots=True)
class WebhookFailed:
    """A verified carrier webhook that could not be applied."""

    carrier: str
    message_id: str
    error: str


def _shipment_rejected(
    alert: ShipmentRejected, env: RenderEnv
) -> dict[str, Any]:
    return {
        "site": tenant_site_name(),
        "order_id": alert.order_id,
        "carrier": alert.carrier,
        "error": alert.error,
        "remedy": alert.remedy,
    }


def _webhook_failed(alert: WebhookFailed, env: RenderEnv) -> dict[str, Any]:
    return {
        "site": tenant_site_name(),
        "carrier": alert.carrier,
        "message_id": alert.message_id,
        "error": alert.error,
    }


staff_email(
    "ops.shipment_rejected",
    area="ops",
    title=_("Courier rejected a shipment"),
    description=_(
        "Tells the store's staff that a carrier refused to create the "
        "shipment of an order, and what to do next."
    ),
    templates=f"{TEMPLATE_DIR}/shipment_rejected",
    subject=gettext_noop(
        "[{site}] {carrier}: shipment creation failed for order {order_id}"
    ),
    build_context=_shipment_rejected,
    sample=lambda env: ShipmentRejected(
        order_id=1234,
        carrier="ACS",
        error="Invalid destination station for the postcode 99999",
        remedy=(
            "Fix the address on the order, then press “Issue ACS voucher "
            "now” on the order's ACS shipment."
        ),
    ),
)
staff_email(
    "ops.webhook_failed",
    area="ops",
    title=_("Carrier webhook failed"),
    description=_(
        "Tells the store's staff that a verified carrier webhook could not "
        "be applied, so a parcel update may have been missed."
    ),
    templates=f"{TEMPLATE_DIR}/webhook_failed",
    subject=gettext_noop(
        "[{site}] {carrier}: webhook processing failed (message {message_id})"
    ),
    build_context=_webhook_failed,
    sample=lambda env: WebhookFailed(
        carrier="BoxNow",
        message_id="3f1c2a90-5b7e-4c1d-9d1e-0a6c1f2b7e11",
        error="KeyError: 'parcelId'",
    ),
)
