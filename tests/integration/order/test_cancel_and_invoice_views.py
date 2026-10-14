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
from order.models.invoice import Invoice, InvoiceCounter, MyDataStatus
from tests.utils.mydata import issuable, mydata_enabled
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
        order = OrderFactory(
            user=self.user, status=OrderStatus.SHIPPED, num_order_items=0
        )
        response = self.client.post(self._url(order.pk), data={})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_cancelling_canceled_order_returns_400(self) -> None:
        order = OrderFactory(
            user=self.user, status=OrderStatus.CANCELED, num_order_items=0
        )
        response = self.client.post(self._url(order.pk), data={})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancelling_pending_order_succeeds(self) -> None:
        order = OrderFactory(
            user=self.user, status=OrderStatus.PENDING, num_order_items=0
        )
        response = self.client.post(self._url(order.pk), data={})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_cancelling_processing_order_succeeds(self) -> None:
        order = OrderFactory(
            user=self.user, status=OrderStatus.PROCESSING, num_order_items=0
        )
        response = self.client.post(self._url(order.pk), data={})
        self.assertEqual(response.status_code, status.HTTP_200_OK)


_MARK = 400001234567890


def _rendered_receipt(
    user, *, mydata_status=MyDataStatus.CONFIRMED, mydata_mark=_MARK
):
    """A shipped receipt order of ``user`` with its rendered receipt, in the
    given AADE state. Issued by a store that issues through the platform,
    by hand, so no signal issues anything on its own. Only a registered
    (CONFIRMED) or cancelled receipt carries a MARK."""
    from order.invoicing import generate_invoice

    order = issuable(OrderFactory(user=user))
    with (
        mydata_enabled(auto_submit=False),
        patch(
            "order.invoicing._render_pdf_bytes",
            return_value=b"%PDF-1.4\n%FAKE\n%EOF",
        ),
    ):
        invoice = generate_invoice(order)
    registered = mydata_status in (
        MyDataStatus.CONFIRMED,
        MyDataStatus.CANCELED,
    )
    Invoice.objects.filter(pk=invoice.pk).update(
        mydata_status=mydata_status,
        mydata_mark=mydata_mark if registered else None,
    )
    invoice.refresh_from_db()
    return order, invoice


_NOT_REGISTERED = (
    MyDataStatus.NOT_SENT,
    MyDataStatus.PENDING,
    MyDataStatus.SUBMITTED,
    MyDataStatus.REJECTED,
    MyDataStatus.CANCELED,
)


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
        """A registered receipt whose PDF is not stored still 404s: the
        customer gets the document, not a row."""
        order = OrderFactory(user=self.user)
        InvoiceCounter.objects.create(year=2026, next_number=1)
        Invoice.objects.create(
            order=order,
            invoice_number="INV-2026-000001",
            mydata_status=MyDataStatus.CONFIRMED,
            mydata_mark=_MARK,
            # document_file intentionally left blank
        )
        response = self.client.get(self._url(order.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_returns_metadata_for_a_registered_receipt(self) -> None:
        order, invoice = _rendered_receipt(self.user)

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
        order = OrderFactory(user=UserAccountFactory(), num_order_items=0)
        response = self.client.get(self._url(order.pk))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_returns_404_for_a_receipt_aade_has_not_registered(self) -> None:
        """A pending, rejected or cancelled receipt is not a valid tax
        document, so the customer never gets it, PDF or not."""
        for mydata_status in _NOT_REGISTERED:
            with self.subTest(mydata_status=mydata_status):
                order, _invoice = _rendered_receipt(
                    self.user, mydata_status=mydata_status
                )
                response = self.client.get(self._url(order.pk))
                self.assertEqual(
                    response.status_code, status.HTTP_404_NOT_FOUND
                )

    def test_returns_404_for_a_confirmed_receipt_without_a_mark(self) -> None:
        order, _invoice = _rendered_receipt(self.user, mydata_mark=None)

        response = self.client.get(self._url(order.pk))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_download_url_points_at_streaming_endpoint(self) -> None:
        """The metadata endpoint must return an absolute URL to the
        Django-gated streaming action — NOT a raw S3 / filesystem URL.
        That's the whole point of routing through Django: identical
        behaviour on both backends + mandatory ownership check."""
        order, _invoice = _rendered_receipt(self.user)

        response = self.client.get(self._url(order.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        download_url = response.data["download_url"]
        self.assertIsNotNone(download_url)
        expected_path = reverse(
            "order-invoice-download", kwargs={"pk": order.pk}
        )
        self.assertIn(expected_path, download_url)


class OrderInvoiceDownloadEndpointTestCase(APITestCase):
    """Covers ``OrderViewSet.invoice_download`` — the streaming endpoint
    backing ``InvoiceDownloadResponseSerializer.download_url``."""

    def setUp(self) -> None:
        self.user = UserAccountFactory()
        self.client.force_authenticate(user=self.user)

    def _url(self, order_id: int) -> str:
        return reverse("order-invoice-download", kwargs={"pk": order_id})

    def test_returns_404_when_no_invoice(self) -> None:
        order = OrderFactory(user=self.user)
        response = self.client.get(self._url(order.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_returns_404_when_invoice_row_has_no_pdf(self) -> None:
        order = OrderFactory(user=self.user)
        InvoiceCounter.objects.create(year=2026, next_number=1)
        Invoice.objects.create(
            order=order,
            invoice_number="INV-2026-000001",
            mydata_status=MyDataStatus.CONFIRMED,
            mydata_mark=_MARK,
        )
        response = self.client.get(self._url(order.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_returns_404_for_a_receipt_aade_has_not_registered(self) -> None:
        for mydata_status in _NOT_REGISTERED:
            with self.subTest(mydata_status=mydata_status):
                order, _invoice = _rendered_receipt(
                    self.user, mydata_status=mydata_status
                )
                response = self.client.get(self._url(order.pk))
                self.assertEqual(
                    response.status_code, status.HTTP_404_NOT_FOUND
                )

    def test_streams_a_registered_receipt_as_attachment(self) -> None:
        order, invoice = _rendered_receipt(self.user)

        response = self.client.get(self._url(order.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(
            f"{invoice.invoice_number}.pdf",
            response["Content-Disposition"],
        )
        # FileResponse streams chunks; assert the body is non-empty and
        # starts with the PDF magic.
        body = b"".join(response.streaming_content)
        self.assertTrue(body.startswith(b"%PDF-"))

    def test_other_user_cannot_download(self) -> None:
        order = OrderFactory(user=UserAccountFactory(), num_order_items=0)
        response = self.client.get(self._url(order.pk))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
