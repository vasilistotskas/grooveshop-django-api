"""The BOX NOW emails: their types, builders and samples.

The customer's mail when the parcel arrives at the locker, an order email
declared through ``order.emails.shipment_email``. ``emails.EmailsConfig``
imports this module at boot; ``shipping_boxnow.tasks`` chooses WHEN to
send.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop

from emails.gates import CarrierGate
from emails.types import RenderEnv
from order.emails import shipment_email
from order.storefront_links import order_page_url
from shipping_boxnow.carrier import BoxNowCarrier
from shipping_boxnow.models import BoxNowLocker, BoxNowShipment

if TYPE_CHECKING:
    from order.models import Order


def _parcel_at_locker(
    shipment: BoxNowShipment, env: RenderEnv
) -> dict[str, Any]:
    locker = shipment.locker
    return {
        "order": shipment.order,
        "order_url": order_page_url(shipment.order, language=env.language),
        "shipment": shipment,
        "locker": locker,
        "locker_address": (
            locker.address_line_1
            if locker is not None
            else shipment.locker_external_id
        ),
        "parcel_id": shipment.parcel_id,
    }


def _sample_parcel_at_locker(order: Order) -> BoxNowShipment:
    return BoxNowShipment(
        order=order,
        locker=BoxNowLocker(
            external_id="apm-sample",
            name="Locker Stadiou",
            title="Locker Stadiou",
            address_line_1="Stadiou 5",
        ),
        parcel_id="9200000001",
    )


shipment_email(
    "shipping.boxnow_at_locker",
    carrier=BoxNowCarrier.code,
    title=_("BOX NOW parcel at the locker"),
    description=_(
        "Sent when the parcel arrives at the locker to be collected."
    ),
    templates="boxnow_parcel_at_locker",
    subject=gettext_noop("Your BOX NOW parcel arrived at the locker"),
    build_context=_parcel_at_locker,
    sample=_sample_parcel_at_locker,
    list_id="boxnow_at_locker",
    gates=(
        CarrierGate(carrier=BoxNowCarrier.code, label=_("BOX NOW carrier")),
    ),
)
