"""Integration tests for GET /api/v1/shipping/options."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from shipping.models import ShippingProvider

pytestmark = pytest.mark.django_db


def test_options_lists_only_active_providers():
    # Seeded providers default to is_active=False — endpoint returns []
    client = APIClient()
    url = reverse("shipping-options")
    response = client.get(url, {"country_code": "GR"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_options_returns_active_provider_kinds():
    # Use boxnow because its adapter is registered during Phase 0;
    # the acs adapter only registers once shipping_acs/ ships in Phase 1.
    ShippingProvider.objects.filter(code="boxnow").update(is_active=True)

    client = APIClient()
    response = client.get(reverse("shipping-options"), {"country_code": "GR"})

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert any(
        opt["providerCode"] == "boxnow" and opt["kind"] == "pickup_point"
        for opt in payload
    )


def test_options_filters_by_country_code():
    ShippingProvider.objects.filter(code="boxnow").update(is_active=True)

    client = APIClient()
    response_gr = client.get(
        reverse("shipping-options"), {"country_code": "GR"}
    )
    response_de = client.get(
        reverse("shipping-options"), {"country_code": "DE"}
    )

    assert response_gr.status_code == status.HTTP_200_OK
    assert any(opt["providerCode"] == "boxnow" for opt in response_gr.json())
    assert response_de.status_code == status.HTTP_200_OK
    assert all(opt["providerCode"] != "boxnow" for opt in response_de.json())


def test_options_skips_provider_with_unregistered_adapter():
    # An "active" provider whose app isn't installed must NOT surface
    # in checkout — otherwise the frontend would render a card that
    # crashes when the user tries to use it.
    ShippingProvider.objects.create(
        code="ghost_carrier",
        name="Ghost Carrier",
        is_active=True,
        supports_home_delivery=True,
        supports_pickup_point=False,
        priority=99,
    )

    client = APIClient()
    response = client.get(reverse("shipping-options"))

    assert all(
        opt["providerCode"] != "ghost_carrier" for opt in response.json()
    )


def test_options_forwards_weight_grams_to_adapter():
    """Endpoint passes ``weight_grams`` through ``available_options``
    into the carrier adapter. Without this thread the ACS live quote
    would always price at the 0.5 kg floor, no matter how heavy the
    cart was."""
    from unittest.mock import patch

    ShippingProvider.objects.filter(code="acs").update(is_active=True)

    client = APIClient()
    captured: dict[str, object] = {}

    def _spy(self, *, order_value_amount, currency, kind, **kwargs):
        captured["weight_grams"] = kwargs.get("weight_grams")
        return (3.5, currency)

    with patch(
        "shipping_acs.carrier.AcsCarrier.calculate_shipping_cost",
        new=_spy,
    ):
        response = client.get(
            reverse("shipping-options"),
            {
                "country_code": "GR",
                "order_value_amount": "10.00",
                "weight_grams": "2400",
            },
        )

    assert response.status_code == status.HTTP_200_OK
    assert captured["weight_grams"] == 2400


def test_options_rejects_negative_weight():
    """``IntegerField(min_value=0)`` rejects weight below zero — the
    request must 400 instead of silently coercing."""
    client = APIClient()
    response = client.get(
        reverse("shipping-options"),
        {"weight_grams": "-1"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_options_rejects_absurd_weight():
    """100 kg+ orders aren't a real cart — bound the param so a
    typo or hostile client can't blow out cache key cardinality."""
    client = APIClient()
    response = client.get(
        reverse("shipping-options"),
        {"weight_grams": "1000000"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
