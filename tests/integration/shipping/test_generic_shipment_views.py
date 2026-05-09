"""Integration tests for the Phase 3 provider-agnostic shipment views.

* ``GET /order/{id}/shipment_label`` — looks up the order's carrier
  adapter and streams the label PDF.
* ``POST /order/{id}/shipment_cancel`` — admin-only cancellation.

Both endpoints exercise the registry dispatch path used by frontends
that have migrated off the per-provider ``boxnow_*`` / ``acs_*``
endpoints.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from order.factories.order import OrderFactory
from shipping.models import ShippingProvider
from shipping_acs.factories import AcsShipmentFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _attach_acs_shipment(order, voucher_no="7227891234"):
    acs = ShippingProvider.objects.get(code="acs")
    order.shipping_provider = acs
    order.shipping_kind = "home_delivery"
    order.save(update_fields=["shipping_provider", "shipping_kind"])
    return AcsShipmentFactory(order=order, voucher_no=voucher_no)


def test_shipment_label_404_when_no_provider_attached():
    user = UserAccountFactory()
    order = OrderFactory(user=user)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(
        reverse("order-shipment-label", kwargs={"pk": order.id})
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_shipment_label_dispatches_to_acs_adapter():
    user = UserAccountFactory()
    order = OrderFactory(user=user)
    _attach_acs_shipment(order)

    client = APIClient()
    client.force_authenticate(user=user)

    with patch(
        "shipping_acs.services.AcsService.fetch_label_bytes"
    ) as mock_fetch:
        mock_fetch.return_value = b"%PDF-1.7 fake bytes"
        response = client.get(
            reverse("order-shipment-label", kwargs={"pk": order.id})
        )

    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"] == "application/pdf"
    assert mock_fetch.called


def test_shipment_cancel_admin_only():
    user = UserAccountFactory(is_staff=False)
    order = OrderFactory(user=user)
    _attach_acs_shipment(order)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        reverse("order-shipment-cancel", kwargs={"pk": order.id})
    )
    assert response.status_code in (
        status.HTTP_403_FORBIDDEN,
        status.HTTP_401_UNAUTHORIZED,
    )


def test_shipment_cancel_dispatches_to_acs_adapter_for_admin():
    admin = UserAccountFactory(is_staff=True, is_superuser=True)
    order = OrderFactory(user=admin)
    _attach_acs_shipment(order)

    client = APIClient()
    client.force_authenticate(user=admin)

    with patch(
        "shipping_acs.services.AcsService.cancel_voucher"
    ) as mock_cancel:
        response = client.post(
            reverse("order-shipment-cancel", kwargs={"pk": order.id}),
            {"reason": "customer changed mind"},
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK
    assert mock_cancel.called


def test_order_detail_exposes_shipment_provider_code():
    """The new SerializerMethodField returns 'acs' / 'boxnow' / null
    so frontends can switch on a single key."""
    user = UserAccountFactory()
    order = OrderFactory(user=user)
    _attach_acs_shipment(order)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(reverse("order-detail", kwargs={"pk": order.id}))

    assert response.status_code == status.HTTP_200_OK
    assert response.json().get("shipmentProviderCode") == "acs"


def test_order_detail_shipment_field_returns_acs_payload():
    user = UserAccountFactory()
    order = OrderFactory(user=user)
    _attach_acs_shipment(order)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(reverse("order-detail", kwargs={"pk": order.id}))

    body = response.json()
    shipment = body.get("shipment")
    assert shipment is not None
    # The payload is the AcsShipmentDetailSerializer dict — check for
    # a known camel-cased field that survives the renderer.
    assert "voucherNo" in shipment or "voucher_no" in shipment
