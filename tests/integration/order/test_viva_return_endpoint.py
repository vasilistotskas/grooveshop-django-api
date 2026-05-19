"""Integration tests for the ``/order/viva_return`` endpoint.

The endpoint translates Viva Wallet's hosted-checkout return URL
params (``?t=<txn>&eventId=<uuid>&s=F``) into the order UUID so the
Nuxt frontend can forward the customer to ``/checkout/success/{uuid}``.

The lookup races the Viva webhook: ``payment_id`` (which equals
``t``) is only set when the webhook arrives, but the customer's
browser redirect can hit this endpoint tens of seconds earlier. The
fallback via ``eventId`` (set to ``order.uuid`` at session-creation
time) closes that gap.
"""

import uuid

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

    def test_lookup_by_event_id_before_webhook(self):
        """Pre-webhook: ``payment_id`` isn't set yet, but ``eventId``
        (the order UUID we sent as ``merchantTrns``) resolves the
        order so the customer isn't stuck on an error page."""
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_method="",
            payment_id="",
            metadata={"viva_order_code": "9999"},
        )

        response = self.client.get(
            self.url, {"t": "txn-xyz-123", "eventId": str(order.uuid)}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["uuid"], str(order.uuid))
        self.assertEqual(response.data["paymentStatus"], "PENDING")

    def test_event_id_without_viva_order_code_is_rejected(self):
        """Defence-in-depth: the ``eventId`` fallback only resolves
        orders that have a ``viva_order_code`` in metadata, so a
        non-Viva order's UUID (which is unguessable but could in
        principle be scraped from elsewhere) cannot be confirmed via
        this endpoint as a Viva order."""
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_method="",
            payment_id="",
            metadata={},
        )

        response = self.client.get(
            self.url, {"t": "txn-xyz-123", "eventId": str(order.uuid)}
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_malformed_event_id_returns_404(self):
        """A garbage ``eventId`` (not a valid UUID) is treated as
        a miss, not a crash."""
        response = self.client.get(
            self.url, {"t": "txn-xyz-123", "eventId": "not-a-uuid"}
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_event_id_returns_404(self):
        """A well-formed but unknown UUID returns 404 (no DB row)."""
        response = self.client.get(
            self.url,
            {"t": "txn-xyz-123", "eventId": str(uuid.uuid4())},
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_both_params_returns_400(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
