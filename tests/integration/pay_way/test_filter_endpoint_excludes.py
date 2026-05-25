"""End-to-end integration: ``/api/v1/pay_way`` honours the admin-
configurable exclusion table when ``shippingProviderCode`` +
``shippingKind`` are supplied.

Pairs with ``tests/unit/pay_way/test_shipping_exclusion.py`` which
covers the service in isolation; this module proves the wiring all
the way from the public URL through to ``PayWayShippingExclusion``.
"""

from __future__ import annotations

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from pay_way.factories import PayWayFactory, PayWayShippingExclusionFactory
from pay_way.models import PayWay
from shipping.enum import ShippingKind
from shipping.factories import ShippingProviderFactory


class PayWayFilterExcludesEndpointTests(APITestCase):
    def setUp(self):
        PayWay.objects.all().delete()

        self.online_pay_way = PayWayFactory(
            active=True,
            is_online_payment=True,
            requires_confirmation=False,
        )
        self.cod_pay_way = PayWayFactory(
            active=True,
            is_online_payment=False,
            requires_confirmation=False,
        )

        # Reuse the seeded "boxnow" / "acs" providers from the
        # conftest reseed fixture — using ``get_or_create`` semantics
        # to stay deterministic under -n auto.
        self.boxnow = ShippingProviderFactory(
            code="boxnow",
            supports_home_delivery=False,
            supports_pickup_point=True,
        )
        self.acs = ShippingProviderFactory(
            code="acs",
            supports_home_delivery=True,
            supports_pickup_point=True,
        )

        self.url = reverse("payway-list")

    def _ids(self, response):
        # ``pagination=false`` returns a raw list; the default
        # ``pageNumber`` strategy returns a ``{results: [...]}``
        # envelope. Handle both so the tests don't need to know
        # which mode the caller asked for.
        data = response.data
        items = data["results"] if isinstance(data, dict) else data
        return {item["id"] for item in items}

    def test_no_exclusion_rows_returns_every_active_pay_way(self):
        response = self.client.get(
            self.url,
            {
                "shippingProviderCode": "boxnow",
                "shippingKind": ShippingKind.PICKUP_POINT.value,
                "pagination": "false",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self._ids(response)
        self.assertIn(self.online_pay_way.id, ids)
        self.assertIn(self.cod_pay_way.id, ids)

    def test_exclusion_row_hides_pay_way_from_storefront_response(self):
        PayWayShippingExclusionFactory(
            pay_way=self.cod_pay_way,
            shipping_provider=self.boxnow,
            shipping_kind=ShippingKind.PICKUP_POINT.value,
        )

        response = self.client.get(
            self.url,
            {
                "shippingProviderCode": "boxnow",
                "shippingKind": ShippingKind.PICKUP_POINT.value,
                "pagination": "false",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self._ids(response)
        self.assertNotIn(self.cod_pay_way.id, ids)
        self.assertIn(self.online_pay_way.id, ids)

    def test_exclusion_for_different_kind_does_not_leak(self):
        # Block the COD pay-way on BoxNow PICKUP_POINT — querying
        # against (acs, PICKUP_POINT) must NOT filter it out.
        PayWayShippingExclusionFactory(
            pay_way=self.cod_pay_way,
            shipping_provider=self.boxnow,
            shipping_kind=ShippingKind.PICKUP_POINT.value,
        )

        response = self.client.get(
            self.url,
            {
                "shippingProviderCode": "acs",
                "shippingKind": ShippingKind.PICKUP_POINT.value,
                "pagination": "false",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self._ids(response)
        self.assertIn(self.cod_pay_way.id, ids)

    def test_query_without_kind_or_provider_skips_exclusions(self):
        # Storefront pages that hit the bare ``/api/v1/pay_way`` (e.g.
        # the admin payment-method list) must see every active pay-way
        # regardless of any exclusion rows that exist.
        PayWayShippingExclusionFactory(
            pay_way=self.cod_pay_way,
            shipping_provider=self.boxnow,
            shipping_kind=ShippingKind.PICKUP_POINT.value,
        )

        response = self.client.get(self.url, {"pagination": "false"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self._ids(response)
        self.assertIn(self.cod_pay_way.id, ids)
        self.assertIn(self.online_pay_way.id, ids)
