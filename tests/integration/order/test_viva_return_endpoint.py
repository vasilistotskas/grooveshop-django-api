"""Integration tests for the ``/order/viva_return`` endpoint.

The endpoint translates Viva Wallet's Smart Checkout return URL
params (``?t=<transaction_id>&s=<order_code>&lang=..&eventId=<int>``)
into the order UUID so the Nuxt frontend can forward the customer to
``/checkout/success/{uuid}``.

The lookup races the Viva webhook: ``payment_id`` (which equals
``t``) is only set when the webhook arrives, but the customer's
browser redirect can hit this endpoint tens of seconds earlier. The
fallback via ``s`` (the Viva order code, stored in
``metadata.viva_order_code`` at session-creation time) closes that
gap.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from order.factories.order import OrderFactory
from order.enum.status import OrderStatus, PaymentStatus


class VivaReturnEndpointTestCase(APITestCase):
    url = reverse("order-viva-return")

    def test_lookup_by_transaction_id_after_webhook(self):
        """Once the webhook has set ``payment_id``, the canonical
        ``t``-based lookup resolves to the order."""
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
            payment_method="viva_wallet",
            payment_id="txn-xyz-123",
            metadata={"viva_order_code": "9999"},
        )

        response = self.client.get(self.url, {"t": "txn-xyz-123"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["uuid"], str(order.uuid))
        self.assertEqual(response.data["paymentStatus"], "COMPLETED")

    def test_lookup_by_order_code_before_webhook(self):
        """Pre-webhook: ``payment_id`` isn't set yet, but ``s`` (the
        Viva order code stored at session creation) resolves the order
        so the customer isn't stuck on an error page during the race."""
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_method="",
            payment_id="",
            metadata={"viva_order_code": "6836925145972608"},
        )

        response = self.client.get(
            self.url,
            {"t": "txn-not-in-db-yet", "s": "6836925145972608"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["uuid"], str(order.uuid))
        self.assertEqual(response.data["paymentStatus"], "PENDING")

    def test_lookup_by_order_code_only(self):
        """Failed transactions may omit ``t`` entirely — ``s`` alone
        must still resolve (the success page then shows the real
        payment status)."""
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_method="",
            payment_id="",
            metadata={"viva_order_code": "7680701046572600"},
        )

        response = self.client.get(self.url, {"s": "7680701046572600"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["uuid"], str(order.uuid))

    def test_transaction_id_wins_over_order_code(self):
        """When both keys are present and ``t`` matches, the
        authoritative ``payment_id`` row is returned even if ``s``
        points elsewhere."""
        order_by_txn = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
            payment_method="viva_wallet",
            payment_id="txn-priority",
            metadata={"viva_order_code": "1111"},
        )
        OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_method="",
            payment_id="",
            metadata={"viva_order_code": "2222"},
        )

        response = self.client.get(self.url, {"t": "txn-priority", "s": "2222"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["uuid"], str(order_by_txn.uuid))

    def test_unknown_order_code_returns_404(self):
        response = self.client.get(self.url, {"s": "0000000000000000"})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_transaction_id_without_order_code_returns_404(self):
        response = self.client.get(self.url, {"t": "txn-nope"})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_both_params_returns_400(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
