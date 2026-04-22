"""Integration tests for the myDATA submission task chain.

Covers the boundaries that unit tests on individual modules can't:

- ``generate_order_invoice`` → ``send_invoice_to_mydata`` chain
  decision (enabled+auto_submit → myDATA, else → direct email)
- ``send_invoice_to_mydata`` → ``send_invoice_email`` chain on success
- Retry behaviour on transport errors
- Short-circuit to email on validation errors (customer still gets
  the pre-transmission PDF)
- ``cancel_mydata_invoice`` task → persisted cancellation MARK

The HTTP layer is mocked at ``MyDataClient`` — we don't need to
exercise :mod:`requests` here, and hitting AADE sandbox would
require live credentials. Tests that need real AADE calls live in a
separate manual smoke-test suite (gated by env var).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from djmoney.money import Money

from extra_settings.models import Setting
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.invoicing import generate_invoice
from order.models.invoice import MyDataStatus
from order.mydata.exceptions import MyDataTransportError
from order.tasks import (
    cancel_mydata_invoice,
    generate_order_invoice,
    send_invoice_to_mydata,
)
from product.factories.product import ProductFactory
from vat.factories import VatFactory


def _enable_mydata() -> None:
    """Flip the runtime toggles + credentials so
    ``MyDataConfig.is_ready()`` returns True."""
    Setting.objects.update_or_create(
        name="MYDATA_ENABLED",
        defaults={"value_type": "bool", "value_bool": True},
    )
    Setting.objects.update_or_create(
        name="MYDATA_AUTO_SUBMIT",
        defaults={"value_type": "bool", "value_bool": True},
    )
    Setting.objects.update_or_create(
        name="MYDATA_USER_ID",
        defaults={"value_type": "string", "value_string": "test-user"},
    )
    Setting.objects.update_or_create(
        name="MYDATA_SUBSCRIPTION_KEY",
        defaults={
            "value_type": "string",
            "value_string": "test-subscription-key",
        },
    )
    Setting.objects.update_or_create(
        name="INVOICE_SELLER_VAT_ID",
        defaults={"value_type": "string", "value_string": "123456789"},
    )


def _make_invoice():
    vat = VatFactory(value=24)
    product = ProductFactory(vat=vat)
    order = OrderFactory(num_order_items=0)
    OrderItemFactory(
        order=order,
        product=product,
        price=Money(Decimal("12.40"), "EUR"),
        quantity=1,
    )
    with patch(
        "order.invoicing._render_pdf_bytes",
        return_value=b"%PDF-1.4 test",
    ):
        invoice = generate_invoice(order)
    return order, invoice


_SUCCESS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ResponseDoc>
    <response>
        <index>1</index>
        <statusCode>Success</statusCode>
        <invoiceUid>ffeeddccbbaa99887766554433221100ffeeddcc</invoiceUid>
        <invoiceMark>800000165789545</invoiceMark>
        <qrUrl>https://www1.aade.gr/mydata/qr/deadbeef</qrUrl>
    </response>
</ResponseDoc>
"""

_CANCELLATION_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ResponseDoc>
    <response>
        <index>1</index>
        <statusCode>Success</statusCode>
        <cancellationMark>900000000000001</cancellationMark>
    </response>
</ResponseDoc>
"""

_VALIDATION_ERROR_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ResponseDoc>
    <response>
        <index>1</index>
        <statusCode>ValidationError</statusCode>
        <errors>
            <code>216</code>
            <message>vatCategory must have value other than 8 for this invoice type</message>
        </errors>
    </response>
</ResponseDoc>
"""


@override_settings(
    SITE_NAME="GrooveShop",
    INFO_EMAIL="support@example.com",
    NUXT_BASE_URL="http://example.com",
    STATIC_BASE_URL="http://example.com",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
)
class GenerateOrderInvoiceChainTestCase(TestCase):
    """The chain decision in ``generate_order_invoice``: when myDATA
    is ready + auto-submit, we queue the submission task; otherwise
    we queue the email directly. Getting this wrong means either
    invoices never reach AADE (regression) or emails fire before the
    MARK is embedded (bad customer experience)."""

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.tasks.send_invoice_to_mydata.delay")
    @patch("order.tasks.send_invoice_email.delay")
    def test_falls_back_to_email_when_mydata_disabled(
        self, mock_email, mock_submit, _mock_render
    ):
        order = OrderFactory(num_order_items=0)
        OrderItemFactory(
            order=order,
            product=ProductFactory(vat=VatFactory(value=24)),
            price=Money(Decimal("10.00"), "EUR"),
            quantity=1,
        )
        # No _enable_mydata() call — integration is off.
        result = generate_order_invoice(order.id)
        self.assertTrue(result)
        mock_email.assert_called_once_with(order.id)
        mock_submit.assert_not_called()

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.tasks.send_invoice_to_mydata.delay")
    @patch("order.tasks.send_invoice_email.delay")
    def test_routes_to_mydata_when_enabled(
        self, mock_email, mock_submit, _mock_render
    ):
        _enable_mydata()
        order = OrderFactory(num_order_items=0)
        OrderItemFactory(
            order=order,
            product=ProductFactory(vat=VatFactory(value=24)),
            price=Money(Decimal("10.00"), "EUR"),
            quantity=1,
        )
        result = generate_order_invoice(order.id)
        self.assertTrue(result)
        mock_submit.assert_called_once_with(order.id)
        # Email is chained from ``send_invoice_to_mydata``, not
        # directly from ``generate_order_invoice``.
        mock_email.assert_not_called()


@override_settings(
    SITE_NAME="GrooveShop",
    INFO_EMAIL="support@example.com",
    NUXT_BASE_URL="http://example.com",
    STATIC_BASE_URL="http://example.com",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
)
class SendInvoiceToMydataTestCase(TestCase):
    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.send_invoice_email.delay")
    def test_success_persists_mark_and_chains_email(
        self, mock_email, mock_send, _mock_render
    ):
        """Happy path: AADE returns Success → MARK + qr_url persisted,
        PDF regenerated (so customer's copy carries the MARK), email
        queued as the last step in the chain."""
        _enable_mydata()
        order, invoice = _make_invoice()
        mock_send.return_value = _SUCCESS_XML

        result = send_invoice_to_mydata(order.id)

        self.assertTrue(result)
        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.CONFIRMED)
        self.assertEqual(invoice.mydata_mark, 800000165789545)
        self.assertEqual(
            invoice.mydata_qr_url,
            "https://www1.aade.gr/mydata/qr/deadbeef",
        )
        mock_email.assert_called_once_with(order.id)

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.send_invoice_email.delay")
    def test_validation_error_short_circuits_to_email(
        self, mock_email, mock_send, _mock_render
    ):
        """Terminal validation errors (e.g. 216 bad vatCategory) must
        still deliver the pre-transmission PDF to the customer —
        leaving them without an invoice while ops reconciles is a
        worse outcome than a slightly non-compliant PDF they can keep."""
        _enable_mydata()
        order, invoice = _make_invoice()
        mock_send.return_value = _VALIDATION_ERROR_XML

        result = send_invoice_to_mydata(order.id)

        self.assertFalse(result)
        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.REJECTED)
        self.assertEqual(invoice.mydata_error_code, "216")
        mock_email.assert_called_once_with(order.id)

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.send_invoice_email.delay")
    def test_transport_error_retries_then_falls_back_to_email(
        self, mock_email, mock_send, _mock_render
    ):
        """Transport errors retry automatically via Celery. Once
        retries are exhausted, we send the email with the pre-
        transmission PDF so the customer isn't left empty-handed."""
        _enable_mydata()
        order, invoice = _make_invoice()
        mock_send.side_effect = MyDataTransportError("upstream 503")

        # Force "last retry reached" state so the task reaches the
        # terminal fallback branch rather than scheduling another retry.
        with patch("order.tasks.send_invoice_to_mydata.max_retries", 0):
            result = send_invoice_to_mydata(order.id)

        self.assertFalse(result)
        mock_email.assert_called_once_with(order.id)
        # Status stays SUBMITTED (not REJECTED) because transport
        # errors are recoverable — the row stays eligible for manual
        # resubmission via the admin action.
        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.SUBMITTED)

    def test_missing_invoice_returns_false(self):
        """An order without an Invoice row (e.g. document_type ≠
        INVOICE) must short-circuit to the email chain, not crash."""
        _enable_mydata()
        order = OrderFactory(num_order_items=0)
        OrderItemFactory(
            order=order,
            product=ProductFactory(vat=VatFactory(value=24)),
            price=Money(Decimal("5.00"), "EUR"),
            quantity=1,
        )
        # No generate_invoice() call — order has no invoice.
        with patch("order.tasks.send_invoice_email.delay") as mock_email:
            result = send_invoice_to_mydata(order.id)
        self.assertFalse(result)
        mock_email.assert_called_once_with(order.id)


@override_settings(
    SITE_NAME="GrooveShop",
    INFO_EMAIL="support@example.com",
    NUXT_BASE_URL="http://example.com",
    STATIC_BASE_URL="http://example.com",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
)
class CancelMydataInvoiceTestCase(TestCase):
    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.mydata.client.MyDataClient.cancel_invoice")
    @patch("order.tasks.send_invoice_email.delay")
    def test_persists_cancellation_mark_on_success(
        self, _mock_email, mock_cancel, mock_send, _mock_render
    ):
        _enable_mydata()
        order, invoice = _make_invoice()
        mock_send.return_value = _SUCCESS_XML
        send_invoice_to_mydata(order.id)

        mock_cancel.return_value = _CANCELLATION_XML
        result = cancel_mydata_invoice(order.id)

        self.assertTrue(result)
        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.CANCELED)
        self.assertEqual(invoice.mydata_cancellation_mark, 900000000000001)

    def test_no_mark_skips_without_error(self):
        _enable_mydata()
        order = OrderFactory(num_order_items=0)
        OrderItemFactory(
            order=order,
            product=ProductFactory(vat=VatFactory(value=24)),
            price=Money(Decimal("5.00"), "EUR"),
            quantity=1,
        )
        # No submission — invoice has no MARK.
        with patch("order.invoicing._render_pdf_bytes", return_value=b"x"):
            generate_invoice(order)

        result = cancel_mydata_invoice(order.id)
        self.assertFalse(result)


@override_settings(
    SITE_NAME="GrooveShop",
    INFO_EMAIL="support@example.com",
    NUXT_BASE_URL="http://example.com",
    STATIC_BASE_URL="http://example.com",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
)
class MydataEmailWithMarkTestCase(TestCase):
    """End-to-end: after a successful submission, the email the
    customer receives must carry the MARK in the attached PDF.
    Pins the regeneration-after-MARK step that completes the happy
    path — if we skip it, the customer's invoice is non-compliant."""

    @patch("order.mydata.client.MyDataClient.send_invoices")
    def test_email_attachment_has_mark_embedded(self, mock_send):
        _enable_mydata()
        mock_send.return_value = _SUCCESS_XML

        # Real render path (no ``_render_pdf_bytes`` patch) so the
        # generated PDF actually contains the MARK placeholder from
        # the template. Use a tiny patched render that captures the
        # context to assert on instead of a full WeasyPrint call.
        captured_contexts: list[dict] = []

        def fake_render(context):
            captured_contexts.append(context)
            return b"%PDF-1.4 fake"

        with patch(
            "order.invoicing._render_pdf_bytes", side_effect=fake_render
        ):
            vat = VatFactory(value=24)
            product = ProductFactory(vat=vat)
            order = OrderFactory(num_order_items=0)
            OrderItemFactory(
                order=order,
                product=product,
                price=Money(Decimal("12.40"), "EUR"),
                quantity=1,
            )
            generate_invoice(order)
            send_invoice_to_mydata(order.id)

        # First render = pre-transmission (no MARK). Second render =
        # after MARK, triggered by ``send_invoice_to_mydata``.
        self.assertGreaterEqual(len(captured_contexts), 2)
        pre, post = captured_contexts[0], captured_contexts[-1]
        self.assertIsNone(pre.get("mydata_mark"))
        self.assertEqual(post["mydata_mark"], 800000165789545)
        # Email was delivered (synchronous via CELERY_TASK_ALWAYS_EAGER
        # in tests) with an attachment.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(mail.outbox[0].attachments), 1)
