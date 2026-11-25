"""The object each order email is about, found from a real order for the
admin preview (``order.emails._PREVIEW_OBJECTS``)."""

from __future__ import annotations

import pytest

from order.emails import (
    _PREVIEW_OBJECTS,
    ORDER_EMAIL_KEYS,
    NothingToPreview,
    preview_object,
)
from order.factories.order import OrderFactory
from shipping.models import ShippingProvider
from shipping_acs.factories import AcsShipmentFactory

pytestmark = pytest.mark.django_db


def test_an_order_without_a_credit_receipt_has_nothing_to_preview():
    order = OrderFactory(num_order_items=0)

    with pytest.raises(NothingToPreview):
        _PREVIEW_OBJECTS["order.credit_issued"](order)


def test_a_carriers_email_previews_that_carriers_shipment():
    """Declared in the carrier's app (``shipment_email``), still found
    from the order through the carrier."""
    order = OrderFactory(num_order_items=0)
    order.shipping_provider = ShippingProvider.objects.get(code="acs")
    order.save(update_fields=["shipping_provider"])
    shipment = AcsShipmentFactory(order=order)

    assert {
        "shipping.acs_out_for_delivery",
        "shipping.boxnow_at_locker",
    } <= ORDER_EMAIL_KEYS
    assert preview_object("shipping.acs_out_for_delivery", order) == shipment
    with pytest.raises(NothingToPreview):
        preview_object("shipping.boxnow_at_locker", order)
