"""``payWayKey`` is what the storefront renders as the payment method.

Before it existed the order pages showed ``order.paymentMethod`` raw —
the GATEWAY code — so a real customer saw "acs_cod" (and, on an order
whose gateway had not written yet, nothing at all, falling back to a
hardcoded ``Method 5``).

Deliberately the KEY and not a server-rendered label. Every route lives
under ``i18n_patterns(prefix_default_language=False)``, so Django's
``LocaleMiddleware`` pins the whole API to ``settings.LANGUAGE_CODE``
and ``Accept-Language`` is inert (measured 2026-09-09). A localised
string from here would lock the storefront to Greek forever; the key
lets ``@nuxtjs/i18n`` translate it the same way the checkout pay-way
list already does.
"""

from __future__ import annotations

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from order.factories.order import OrderFactory
from order.serializers.order import OrderDetailSerializer, OrderSerializer
from pay_way.enum.pay_way import PayWayEnum
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay


class PayWayKeyFieldTests(APITestCase):
    def setUp(self):
        pay_way = PayWayFactory(
            active=True,
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH.value,
        )
        pay_way.translations.update(name=PayWayEnum.PAY_ON_DELIVERY.value)
        cache.clear()
        self.pay_way = PayWay.objects.get(pk=pay_way.pk)
        # A GUEST order: ``IsOwnerOrAdminOrGuest`` grants the
        # by-uuid route only when ``order.user`` is None and the
        # uuid is echoed back, which is the real success-page flow.
        self.order = OrderFactory(
            pay_way=self.pay_way, payment_method="acs_cod", user=None
        )

    def test_both_tiers_expose_it(self):
        """List AND detail — the account order list renders it too."""
        self.assertIn("pay_way_key", OrderSerializer.Meta.fields)
        self.assertIn("pay_way_key", OrderDetailSerializer.Meta.fields)

    def test_it_is_read_only(self):
        """``Order.save()`` owns the column. Writable would let a client
        pin a key contradicting ``pay_way``: the snapshot is only
        rewritten when the pay way itself changes, so a bogus value
        posted alongside an unchanged pay way would survive."""
        self.assertIn("pay_way_key", OrderSerializer.Meta.read_only_fields)

        serializer = OrderSerializer(
            instance=self.order,
            data={"pay_way_key": PayWayEnum.CREDIT_CARD.value},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("pay_way_key", serializer.validated_data)

    def test_the_payload_carries_the_key_not_the_gateway_code(self):
        response = self.client.get(
            reverse(
                "order-retrieve-by-uuid", kwargs={"uuid": str(self.order.uuid)}
            ),
            {"uuid": str(self.order.uuid)},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["payWayKey"], PayWayEnum.PAY_ON_DELIVERY.value)
        # The gateway code is still there for anything that needs to
        # know who took the money — it is simply not the label.
        self.assertEqual(body["paymentMethod"], "acs_cod")

    def test_it_is_still_there_once_the_pay_way_is_deleted(self):
        self.pay_way.delete()

        response = self.client.get(
            reverse(
                "order-retrieve-by-uuid", kwargs={"uuid": str(self.order.uuid)}
            ),
            {"uuid": str(self.order.uuid)},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIsNone(body["payWay"])
        self.assertEqual(body["payWayKey"], PayWayEnum.PAY_ON_DELIVERY.value)

    def test_an_order_with_no_pay_way_serialises_a_blank_key(self):
        """The case that made ``allow_blank`` load-bearing.

        The first generated client schema had ``payWayKey`` as a bare
        required enum with no blank member, so this payload would have
        failed ``parseDataAs`` and 422'd the order page outright.
        """
        order = OrderFactory(pay_way=None, payment_method="", user=None)

        response = self.client.get(
            reverse("order-retrieve-by-uuid", kwargs={"uuid": str(order.uuid)}),
            {"uuid": str(order.uuid)},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["payWayKey"], "")
