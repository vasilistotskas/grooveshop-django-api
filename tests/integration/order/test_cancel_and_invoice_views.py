"""Integration tests for the cancel (400-on-bad-state) and invoice
download endpoints added by the live-notifications + invoicing slices.

The frontend classifies ``400`` as a "conflict — refresh and toast"
scenario and ``5xx`` as an unexpected error. Prior to the fix the
backend let ``OrderCancellationError`` fall through to 500, so a user
clicking cancel on an already-shipped order saw "unexpected error"
instead of a clear "cannot cancel, refreshing". These tests pin the
contract so a future refactor doesn't regress it.
"""

from __future__ import annotations

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.models.invoice import Invoice, InvoiceCounter
from user.factories.account import UserAccountFactory


class CancelOrderStateTransitionTestCase(APITestCase):
    def setUp(self) -> None:
        self.user = UserAccountFactory()
        self.client.force_authenticate(user=self.user)

    def _url(self, order_id: int) -> str:
        return reverse("order-cancel", kwargs={"pk": order_id})

    def test_cancelling_shipped_order_returns_400(self) -> None:
        """Only PENDING / PROCESSING orders can be canceled. A SHIPPED
        order raising ``OrderCancellationError`` must surface as 400,
        not 500 — the frontend relies on 400 to switch to its
        conflict-refresh UX."""
        order = OrderFactory(user=self.user, status=OrderStatus.SHIPPED)
        response = self.client.post(self._url(order.pk), data={})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_cancelling_canceled_order_returns_400(self) -> None:
        order = OrderFactory(user=self.user, status=OrderStatus.CANCELED)
        response = self.client.post(self._url(order.pk), data={})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancelling_pending_order_succeeds(self) -> None:
        order = OrderFactory(user=self.user, status=OrderStatus.PENDING)
        response = self.client.post(self._url(order.pk), data={})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_cancelling_processing_order_succeeds(self) -> None:
        order = OrderFactory(user=self.user, status=OrderStatus.PROCESSING)
        response = self.client.post(self._url(order.pk), data={})
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class OrderInvoiceEndpointTestCase(APITestCase):
    def setUp(self) -> None:
        self.user = UserAccountFactory()
        self.client.force_authenticate(user=self.user)

    def _url(self, order_id: int) -> str:
        return reverse("order-invoice", kwargs={"pk": order_id})

    def test_returns_404_when_no_invoice_exists(self) -> None:
        order = OrderFactory(user=self.user)
        response = self.client.get(self._url(order.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_returns_404_when_invoice_row_exists_but_no_pdf(self) -> None:
        """Invoice row without a rendered PDF should still 404 —
        ``has_invoice`` / ``has_document`` is the real gate."""
        order = OrderFactory(user=self.user)
        InvoiceCounter.objects.create(year=2026, next_number=1)
        Invoice.objects.create(
            order=order,
            invoice_number="INV-2026-000001",
            # document_file intentionally left blank
        )
        response = self.client.get(self._url(order.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch(
        "order.invoicing._render_pdf_bytes",
        return_value=b"%PDF-1.4\n%...\n%EOF",
    )
    def test_returns_metadata_when_pdf_exists(self, _mock_render) -> None:
        from order.invoicing import generate_invoice

        order = OrderFactory(user=self.user)
        invoice = generate_invoice(order)

        response = self.client.get(self._url(order.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["invoice_number"], invoice.invoice_number
        )
        # download_url is present (may be a signed S3 URL or a relative
        # path depending on storage backend — we only assert it's
        # populated with a non-null value).
        self.assertIsNotNone(response.data["download_url"])

    def test_other_user_cannot_fetch_invoice(self) -> None:
        """Ownership check — ``IsOwnerOrAdminOrGuest`` applies via the
        viewset's standard ``get_object``."""
        order = OrderFactory(user=UserAccountFactory())
        response = self.client.get(self._url(order.pk))
        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )
