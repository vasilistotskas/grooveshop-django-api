"""``OrderService._assign_shipping_provider`` gives the order the delivery
its quote priced (``CheckoutQuote.shipping_choice``): the carrier the
quote resolved, never a second resolution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from order.checkout.quote import ShippingChoice
from order.services import OrderService

pytestmark = pytest.mark.django_db


def _assigned(choice: ShippingChoice | None) -> dict:
    order_data: dict = {}
    OrderService._assign_shipping_provider(
        order_data, SimpleNamespace(shipping_choice=choice)
    )
    return order_data


def test_the_quoted_carrier_is_assigned():
    order_data = _assigned(
        ShippingChoice(
            kind="pickup_point", provider_code="boxnow", country_id="GR"
        )
    )

    assert order_data["shipping_provider"].code == "boxnow"
    assert order_data["shipping_kind"] == "pickup_point"


def test_a_home_delivery_takes_the_carrier_the_quote_resolved(monkeypatch):
    # The quote resolved it; the order must not ask again.
    from shipping.services import ShippingService

    def _never(*args, **kwargs):
        raise AssertionError("resolved a second time")

    monkeypatch.setattr(
        ShippingService, "resolve_home_delivery_provider", _never
    )

    order_data = _assigned(
        ShippingChoice(
            kind="home_delivery", provider_code="flat_rate", country_id="GR"
        )
    )

    assert order_data["shipping_provider"].code == "flat_rate"


def test_a_delivery_without_a_carrier_stays_unlinked():
    order_data = _assigned(
        ShippingChoice(
            kind="home_delivery", provider_code=None, country_id="GR"
        )
    )

    assert "shipping_provider" not in order_data
    assert order_data["shipping_kind"] == "home_delivery"


def test_a_quote_without_a_delivery_is_a_programming_error():
    with pytest.raises(ValueError, match="no carrier"):
        _assigned(None)
