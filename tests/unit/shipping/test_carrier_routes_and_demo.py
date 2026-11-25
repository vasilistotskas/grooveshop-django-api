"""What ``core`` and ``devtools`` ask a carrier, instead of naming it.

A carrier names its storefront API's URL conf and the one its courier
calls; ``shipping.urls`` and ``shipping.webhook_urls`` mount every
registered carrier's, so the root URL conf names none. The demo seed asks
the order's carrier for the shipment it writes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.urls import URLResolver, resolve
from djmoney.money import Money

from order.factories.order import OrderFactory
from shipping import urls as shipping_urls
from shipping import webhook_urls
from shipping.demo import ShipmentRow
from shipping.interfaces import get_provider, registered_providers

NOW = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)


def _included(urlpatterns) -> set[str]:
    return {
        pattern.urlconf_name.__name__
        for pattern in urlpatterns
        if isinstance(pattern, URLResolver)
    }


class TestRoutes:
    def test_every_carriers_api_is_mounted_with_shipping(self):
        assert _included(shipping_urls.urlpatterns) == {
            carrier.api_urlconf
            for carrier in registered_providers()
            if carrier.api_urlconf is not None
        }
        assert {"shipping_acs.urls", "shipping_boxnow.urls"} <= _included(
            shipping_urls.urlpatterns
        )

    def test_every_carriers_webhook_is_mounted_at_the_root(self):
        assert _included(webhook_urls.urlpatterns) == {
            carrier.webhook_urlconf
            for carrier in registered_providers()
            if carrier.webhook_urlconf is not None
        }
        assert resolve("/boxnow/webhook/").url_name == "boxnow-webhook"

    def test_the_flat_rate_carrier_routes_nothing(self):
        flat = get_provider("flat_rate")

        assert flat.api_urlconf is None
        assert flat.webhook_urlconf is None


class TestDemoShipment:
    @pytest.mark.django_db
    def test_a_carrier_without_shipment_rows_writes_none(self):
        with pytest.raises(NotImplementedError):
            get_provider("flat_rate").create_demo_shipment(
                OrderFactory.build(),
                ShipmentRow("flat_rate", "delivered", "DEMO0000000001"),
                placed_at=NOW,
                now=NOW,
                collect=Money(0, "EUR"),
                weight_grams=500,
            )
