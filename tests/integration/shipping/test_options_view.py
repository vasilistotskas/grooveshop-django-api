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


def test_options_returns_active_provider_kinds(boxnow_configured_tenant):
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


def test_options_filters_by_country_code(boxnow_configured_tenant):
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


def test_options_forwards_weight_grams_to_adapter(acs_configured_tenant):
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


def _name_it(pay_way, enum_value: str):
    """Write the enum key and hand back an instance that can see it.

    ``.update()`` goes straight to SQL, past BOTH of parler's caches —
    the per-instance one and the shared backend ``PARLER_ENABLE_CACHING``
    fills on first read. Without the clear, every assertion here reads
    whatever name the factory's Iterator happened to assign and the test
    lies. Same trap as ``tests/integration/pay_way/test_display_name.py``.
    """
    from django.core.cache import cache

    from pay_way.models import PayWay

    pay_way.translations.update(name=enum_value)
    cache.clear()
    return PayWay.objects.get(pk=pay_way.pk)


class TestPayWaysPerOption:
    """Each option advertises what it can actually settle.

    BOX NOW Αντικαταβολή is reachable ONLY through a BoxNow locker, and
    the payment step is a step LATER — so a shopper who never picks one
    has no way to learn the method exists. The delivery card can say
    so, but only if the option carries the answer.

    The rules are not restated here: ``available_options`` calls the
    same ``PayWayService.filter_by_carrier`` the checkout calls, so a
    delivery card cannot advertise something the payment step would
    then refuse. These tests pin that they agree.
    """

    def test_each_option_lists_the_pay_ways_it_can_settle(
        self, boxnow_configured_tenant
    ):
        from pay_way.enum.pay_way import PayWayEnum
        from pay_way.enum.settlement import PaySettlement
        from pay_way.factories import PayWayFactory

        potg = PayWayFactory(
            active=True,
            provider_code="boxnow_pay_on_the_go",
            settlement=PaySettlement.CARRIER_TERMINAL.value,
        )
        potg = _name_it(potg, PayWayEnum.BOX_NOW_PAY_ON_THE_GO.value)
        ShippingProvider.objects.filter(code="boxnow").update(is_active=True)

        client = APIClient()
        response = client.get(
            reverse("shipping-options"), {"country_code": "GR"}
        )

        assert response.status_code == status.HTTP_200_OK
        locker = next(
            opt
            for opt in response.json()
            if opt["providerCode"] == "boxnow" and opt["kind"] == "pickup_point"
        )
        assert PayWayEnum.BOX_NOW_PAY_ON_THE_GO.value in [
            p["name"] for p in locker["payWays"]
        ]

    def test_a_courier_cash_pay_way_is_not_offered_on_a_locker(
        self, boxnow_configured_tenant
    ):
        """The exact pairing the whole settlement split exists to stop:
        a BoxNow locker cannot take cash from a courier who is never
        there. If this ever passes, the delivery card is advertising a
        combination the payment step refuses."""
        from pay_way.enum.pay_way import PayWayEnum
        from pay_way.enum.settlement import PaySettlement
        from pay_way.factories import PayWayFactory

        cod = PayWayFactory(
            active=True,
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH.value,
        )
        cod = _name_it(cod, PayWayEnum.PAY_ON_DELIVERY.value)
        ShippingProvider.objects.filter(code="boxnow").update(is_active=True)

        client = APIClient()
        response = client.get(
            reverse("shipping-options"), {"country_code": "GR"}
        )

        locker = next(
            opt
            for opt in response.json()
            if opt["providerCode"] == "boxnow" and opt["kind"] == "pickup_point"
        )
        assert PayWayEnum.PAY_ON_DELIVERY.value not in [
            p["name"] for p in locker["payWays"]
        ]

    def test_an_inactive_pay_way_is_never_advertised(
        self, boxnow_configured_tenant
    ):
        from pay_way.enum.pay_way import PayWayEnum
        from pay_way.enum.settlement import PaySettlement
        from pay_way.factories import PayWayFactory

        hidden = PayWayFactory(
            active=False,
            provider_code="boxnow_pay_on_the_go",
            settlement=PaySettlement.CARRIER_TERMINAL.value,
        )
        hidden = _name_it(hidden, PayWayEnum.BOX_NOW_PAY_ON_THE_GO.value)
        ShippingProvider.objects.filter(code="boxnow").update(is_active=True)

        client = APIClient()
        response = client.get(
            reverse("shipping-options"), {"country_code": "GR"}
        )

        for opt in response.json():
            assert PayWayEnum.BOX_NOW_PAY_ON_THE_GO.value not in [
                p["name"] for p in opt["payWays"]
            ]

    def test_the_field_is_always_present(self, boxnow_configured_tenant):
        """The storefront compares sets across rows; a missing key would
        make it treat "no data" as "nothing available"."""
        ShippingProvider.objects.filter(code="boxnow").update(is_active=True)

        client = APIClient()
        response = client.get(
            reverse("shipping-options"), {"country_code": "GR"}
        )

        assert response.json()
        for opt in response.json():
            assert isinstance(opt["payWays"], list)
