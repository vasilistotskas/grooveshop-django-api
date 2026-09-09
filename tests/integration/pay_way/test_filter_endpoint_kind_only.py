"""``/api/v1/pay_way?shippingKind=…`` filters without a carrier code.

The storefront's checkout sends this exact shape on the home-delivery
step, because ``home_delivery`` is provider-agnostic there and has no
carrier code to send (``carrierForMethod`` returns null for it). The
endpoint answered with the full pay-way list, so BOX NOW PAY ON THE GO
rendered as a third radio button next to courier cash-on-delivery —
observed in the browser on staging 2026-09-09 while the API payload for
the *paired* query was correct all along, which is why the service-level
tests never caught it.

``tests/integration/pay_way/test_filter_by_shipping_kind.py`` covers the
intersection rule itself; this module only proves the URL reaches it.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay
from shipping.models import ShippingProvider


class PayWayKindOnlyEndpointTests(APITestCase):
    def setUp(self):
        PayWay.objects.all().delete()

        self.courier_cash = PayWayFactory(
            active=True,
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH,
        )
        self.pay_on_the_go = PayWayFactory.create_carrier_terminal_payment()
        self.online = PayWayFactory.create_online_payment()

        self.boxnow = self._activate(
            "boxnow",
            supports_home_delivery=False,
            supports_pickup_point=True,
        )
        self.acs = self._activate(
            "acs",
            supports_home_delivery=True,
            supports_pickup_point=False,
        )
        self.url = reverse("payway-list")

    @staticmethod
    def _activate(code: str, **flags):
        """Force a provider row into the state this test needs.

        Both states occur across xdist workers, so neither can be
        assumed: ``shipping/migrations/0002_seed_providers`` leaves
        ``acs``/``boxnow`` rows in a fresh test DB, and a
        ``transaction=True`` test flushes them for every later test on
        that worker's reused DB.

        NOT ``ShippingProviderFactory``: its ``django_get_or_create``
        on ``code`` returns the seeded row and silently DROPS these
        flags, leaving both carriers inactive — which makes every
        assertion here pass vacuously, since an inactive carrier is
        not a candidate and the filter falls through.
        """
        updated = ShippingProvider.objects.filter(code=code).update(
            is_active=True, **flags
        )
        if not updated:
            ShippingProvider.objects.create(
                code=code, name=code, is_active=True, **flags
            )
        return ShippingProvider.objects.get(code=code)

    @staticmethod
    def _configured():
        """A tenant with working carriers and payment credentials.

        Three independent gates would otherwise empty the list for
        reasons unrelated to shipping kind: both carriers hide every
        kind from an uncredentialed tenant, and the viewset drops
        pay-ways whose payment provider the tenant holds no keys for
        (the factory's online row is Stripe).
        """
        stack = ExitStack()
        stack.enter_context(
            patch("shipping_acs.config.is_configured", return_value=True)
        )
        stack.enter_context(
            patch("shipping_boxnow.services.is_configured", return_value=True)
        )
        stack.enter_context(
            patch(
                "pay_way.services.PayWayService.is_provider_configured",
                return_value=True,
            )
        )
        return stack

    def _ids(self, response):
        data = response.data
        items = data["results"] if isinstance(data, dict) else data
        return {item["id"] for item in items}

    def test_home_delivery_alone_drops_the_locker_pay_way(self):
        with self._configured():
            response = self.client.get(
                self.url,
                {"active": "true", "shippingKind": "home_delivery"},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self._ids(response)
        self.assertNotIn(self.pay_on_the_go.id, ids)
        self.assertIn(self.courier_cash.id, ids)
        self.assertIn(self.online.id, ids)

    def test_pickup_point_alone_drops_courier_cash(self):
        with self._configured():
            response = self.client.get(
                self.url,
                {"active": "true", "shippingKind": "pickup_point"},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self._ids(response)
        self.assertNotIn(self.courier_cash.id, ids)
        self.assertIn(self.pay_on_the_go.id, ids)

    def test_a_provider_code_alone_still_filters_nothing(self):
        """Unchanged contract: the kind is the half that carries rules.

        A carrier's constraints are per-kind — BoxNow's locker rule
        does not describe its (disabled) home delivery — so a provider
        with no kind cannot be narrowed and must pass through rather
        than guess a kind.
        """
        with self._configured():
            response = self.client.get(
                self.url,
                {"active": "true", "shippingProviderCode": "boxnow"},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._ids(response),
            {self.courier_cash.id, self.pay_on_the_go.id, self.online.id},
        )
