"""Country-scoped ``PayWayShippingExclusion``.

The webside store opens Cyprus as BoxNow-lockers-only and wants card
payment there, not BOX NOW PAY ON THE GO, while Greece keeps offering
it. A row's ``country`` scopes it to one delivery country; ``NULL``
keeps the original every-country meaning.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import cast
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from country.factories import CountryFactory
from country.models import Country
from pay_way.factories import PayWayFactory, PayWayShippingExclusionFactory
from pay_way.models import PayWay, PayWayShippingExclusion
from pay_way.services import PayWayService
from shipping.enum import ShippingKind
from shipping.factories import ShippingRateFactory
from shipping.models import ShippingProvider
from shipping.services import ShippingService

LOCKER = ShippingKind.PICKUP_POINT.value


def _country(code: str) -> Country:
    """The seeded ISO row when present, else a factory-made one."""
    found = Country.objects.filter(alpha_2=code).first()
    if found is not None:
        return found
    return cast("Country", CountryFactory(alpha_2=code, alpha_3=f"{code}X"))


def _activate(code: str, **flags) -> ShippingProvider:
    flags.setdefault("supports_home_delivery", code == "acs")
    flags.setdefault("supports_pickup_point", code == "boxnow")
    updated = ShippingProvider.objects.filter(code=code).update(
        is_active=True, **flags
    )
    if not updated:
        ShippingProvider.objects.create(
            code=code, name=code, is_active=True, **flags
        )
    return ShippingProvider.objects.get(code=code)


def _carriers_configured():
    stack = ExitStack()
    stack.enter_context(
        patch("shipping_acs.config.is_configured", return_value=True)
    )
    stack.enter_context(
        patch("shipping_boxnow.services.is_configured", return_value=True)
    )
    return stack


class ExclusionCountryModelTests(TestCase):
    def setUp(self):
        self.pay_way = PayWayFactory.create_carrier_terminal_payment()
        self.provider = _activate("boxnow")
        self.cy = _country("CY")
        self.gr = _country("GR")

    def _make(self, country=None):
        return PayWayShippingExclusion.objects.create(
            pay_way=self.pay_way,
            shipping_provider=self.provider,
            shipping_kind=LOCKER,
            country=country,
        )

    def test_a_row_without_a_country_is_the_every_country_rule(self):
        row = self._make()
        self.assertIsNone(row.country_id)

    def test_two_every_country_rows_for_one_combo_are_refused(self):
        self._make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._make()

    def test_the_same_country_twice_is_refused(self):
        self._make(self.cy)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._make(self.cy)

    def test_a_null_row_and_country_rows_coexist(self):
        self._make()
        self._make(self.cy)
        self._make(self.gr)
        self.assertEqual(PayWayShippingExclusion.objects.count(), 3)


class ExclusionCountryServiceTests(TestCase):
    def setUp(self):
        self.potg = PayWayFactory.create_carrier_terminal_payment()
        self.online = PayWayFactory.create_online_payment()
        self.boxnow = _activate("boxnow")
        self.cy = _country("CY")
        self.gr = _country("GR")

    def _carrier(self, country_code):
        qs = PayWayService.filter_by_carrier(
            PayWay.objects.all(),
            provider_code="boxnow",
            shipping_kind=LOCKER,
            country_code=country_code,
        )
        return set(qs.values_list("id", flat=True))

    def _exclude(self, country=None):
        PayWayShippingExclusionFactory(
            pay_way=self.potg,
            shipping_provider=self.boxnow,
            shipping_kind=LOCKER,
            country=country,
        )

    def test_an_every_country_row_applies_in_every_country(self):
        self._exclude()
        for code in ("CY", "GR", None):
            self.assertNotIn(self.potg.id, self._carrier(code))
            self.assertIn(self.online.id, self._carrier(code))

    def test_a_cyprus_row_applies_to_cyprus_only(self):
        self._exclude(self.cy)
        self.assertNotIn(self.potg.id, self._carrier("CY"))
        self.assertIn(self.potg.id, self._carrier("GR"))
        self.assertIn(self.online.id, self._carrier("CY"))

    def test_the_country_code_is_case_insensitive(self):
        self._exclude(self.cy)
        self.assertNotIn(self.potg.id, self._carrier("cy"))

    def test_an_unknown_country_ignores_country_scoped_rows(self):
        """No country to evaluate a scoped rule against: only the
        every-country rows apply, so nothing is guessed."""
        self._exclude(self.cy)
        self.assertIn(self.potg.id, self._carrier(None))

    def test_filter_by_shipping_kind_intersects_with_the_country(self):
        _activate("boxnow")
        self._exclude(self.cy)
        with _carriers_configured():
            cy = PayWayService.filter_by_shipping_kind(
                PayWay.objects.all(),
                shipping_kind=LOCKER,
                country_code="CY",
            )
            gr = PayWayService.filter_by_shipping_kind(
                PayWay.objects.all(),
                shipping_kind=LOCKER,
                country_code="GR",
            )
        self.assertNotIn(self.potg.id, set(cy.values_list("id", flat=True)))
        self.assertIn(self.potg.id, set(gr.values_list("id", flat=True)))


class ExclusionCountryCallerTests(APITestCase):
    def setUp(self):
        self.potg = PayWayFactory.create_carrier_terminal_payment()
        self.card = PayWayFactory.create_online_payment(
            provider_code="viva_wallet"
        )
        self.boxnow = _activate("boxnow")
        self.cy = _country("CY")
        self.gr = _country("GR")
        PayWayShippingExclusionFactory(
            pay_way=self.potg,
            shipping_provider=self.boxnow,
            shipping_kind=LOCKER,
            country=self.cy,
        )

    def _ids(self, response):
        data = response.data
        items = data["results"] if isinstance(data, dict) else data
        return {item["id"] for item in items}

    def test_pay_ways_for_drops_the_excluded_method_in_cyprus_only(self):
        cy = ShippingService._pay_ways_for(
            "boxnow", ShippingKind.PICKUP_POINT, "CY"
        )
        gr = ShippingService._pay_ways_for(
            "boxnow", ShippingKind.PICKUP_POINT, "GR"
        )
        self.assertNotIn(self.potg.id, {p["id"] for p in cy})
        self.assertIn(self.card.id, {p["id"] for p in cy})
        self.assertIn(self.potg.id, {p["id"] for p in gr})

    def test_available_options_carry_the_country_scoped_pay_ways(self):
        ShippingRateFactory(provider=self.boxnow, country=self.cy, kind=LOCKER)
        ShippingRateFactory(provider=self.boxnow, country=self.gr, kind=LOCKER)
        with _carriers_configured():
            cy = ShippingService.available_options(country_code="CY")
            gr = ShippingService.available_options(country_code="GR")

        def locker_ids(options):
            opt = next(o for o in options if o["provider_code"] == "boxnow")
            return {p["id"] for p in opt["pay_ways"]}

        self.assertNotIn(self.potg.id, locker_ids(cy))
        self.assertIn(self.potg.id, locker_ids(gr))

    def test_the_pay_way_list_honours_the_country_param(self):
        url = reverse("payway-list")
        base = {
            "shippingProviderCode": "boxnow",
            "shippingKind": LOCKER,
            "pagination": "false",
        }
        # The list hides a PSP the tenant holds no credentials for.
        with patch(
            "pay_way.services.PayWayService.is_provider_configured",
            return_value=True,
        ):
            cy = self.client.get(url, {**base, "country": "CY"})
            gr = self.client.get(url, {**base, "country": "GR"})
            bare = self.client.get(url, base)
        self.assertEqual(cy.status_code, status.HTTP_200_OK)
        self.assertNotIn(self.potg.id, self._ids(cy))
        self.assertIn(self.card.id, self._ids(cy))
        self.assertIn(self.potg.id, self._ids(gr))
        self.assertIn(self.potg.id, self._ids(bare))

    def test_order_create_refuses_the_method_for_a_cyprus_address(self):
        from order.views.order import OrderViewSet

        view = OrderViewSet()
        submitted = {
            "shipping_provider_code": "boxnow",
            "shipping_kind": LOCKER,
            "country_id": "CY",
        }
        with self.assertRaises(ValidationError) as caught:
            view._validate_pay_way_for_order(self.potg, submitted)
        self.assertIn("pay_way_id", caught.exception.detail)

        # Greece is unaffected, and card stays valid in Cyprus.
        view._validate_pay_way_for_order(
            self.potg, {**submitted, "country_id": "GR"}
        )
        with patch(
            "pay_way.services.PayWayService.is_provider_configured",
            return_value=True,
        ):
            view._validate_pay_way_for_order(self.card, submitted)

    def test_payment_intent_refuses_the_method_for_a_cyprus_address(self):
        from cart.factories.cart import CartFactory
        from user.factories.account import UserAccountFactory

        user = UserAccountFactory(num_addresses=0)
        CartFactory(user=user, num_cart_items=1)
        self.client.force_authenticate(user=user)

        response = self.client.post(
            reverse("cart-create-payment-intent"),
            {
                "pay_way_id": self.potg.id,
                "shipping_provider_code": "boxnow",
                "shipping_kind": LOCKER,
                "country_id": "CY",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["reason"], "pay_way_unavailable")
