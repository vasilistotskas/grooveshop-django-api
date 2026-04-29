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
