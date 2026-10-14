"""Integration tests for the myDATA submission task chain.

Covers the boundaries that unit tests on individual modules can't:

- ``generate_order_invoice`` issues a shipped receipt order's receipt
  and hands it to ``send_invoice_to_mydata`` (and issues nothing else)
- ``send_invoice_to_mydata`` → ``send_invoice_email`` chain on success
- Retry behaviour on transport errors
- A refused or unanswered receipt is never emailed: a receipt AADE has
  not registered is not issued
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

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.invoicing import generate_invoice
from order.models.invoice import Invoice, MyDataStatus
from order.mydata.exceptions import MyDataTransportError
from order.tasks import (
    cancel_mydata_invoice,
    generate_order_invoice,
    send_invoice_to_mydata,
)
from product.factories.product import ProductFactory
from tests.utils.mydata import issuable, mydata_enabled
from vat.factories import VatFactory


def _make_invoice():
    # Pin document_type=RECEIPT so the Tier B guard (B2B requires
    # billing_vat_id) doesn't kick in — OrderFactory otherwise rolls
    # a random document type and would intermittently flag orders as
    # INVOICE without a buyer VAT. These tests exercise the 11.1
    # retail path; Tier B B2B flows have their own fixtures.
    #
    # Pin status=PENDING + payment_status=PENDING so OrderFactory's
    # random rolls cannot trip ``handle_order_completed`` /
    # ``handle_order_status_changed`` during the .create() call. Under
    # ``CELERY_TASK_ALWAYS_EAGER=True`` + the on_commit-immediate
    # fixture, those handlers fire ``generate_order_invoice.delay`` and
    # ``send_order_status_update_email.delay`` synchronously inside the
    # factory call, which both pollutes mocks (mock_email called from
    # the chain before the test body runs) and renders the invoice
    # ahead of the test's own ``generate_invoice`` (changing
    # ``captured_contexts`` length). Pinning here keeps the test
    # body's signals deterministic; ``issuable`` then ships it with no
    # signal, the only kind of order a receipt is issued for.
    from order.enum.document_type import OrderDocumentTypeEnum

    vat = VatFactory(value=24)
    product = ProductFactory(vat=vat)
    order = OrderFactory(
        num_order_items=0,
        document_type=OrderDocumentTypeEnum.RECEIPT,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
    )
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
        invoice = generate_invoice(issuable(order))
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

_TECHNICAL_ERROR_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ResponseDoc>
    <response>
        <index>1</index>
        <statusCode>TechnicalError</statusCode>
        <errors>
            <code>500</code>
            <message>AADE gateway temporarily unavailable</message>
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
    """``generate_order_invoice`` issues the receipt of a receipt order that
    has shipped, then hands it to the myDATA submission; the email is
    chained from that submission once the MARK is persisted, never from
    here."""

    def _shipped_order(self, **kwargs):
        from order.enum.document_type import OrderDocumentTypeEnum

        kwargs.setdefault("document_type", OrderDocumentTypeEnum.RECEIPT)
        kwargs.setdefault("status", OrderStatus.SHIPPED)
        kwargs.setdefault("payment_status", PaymentStatus.PENDING)
        order = OrderFactory(num_order_items=0, **kwargs)
        OrderItemFactory(
            order=order,
            product=ProductFactory(vat=VatFactory(value=24)),
            price=Money(Decimal("10.00"), "EUR"),
            quantity=1,
        )
        return order

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.tasks.send_invoice_to_mydata.delay")
    @patch("order.tasks.send_invoice_email.delay")
    def test_issues_nothing_when_mydata_is_disabled(
        self, mock_email, mock_submit, _mock_render
    ):
        order = self._shipped_order()
        # No mydata_enabled() — the integration is off.
        self.assertFalse(generate_order_invoice(order.id))
        mock_email.assert_not_called()
        mock_submit.assert_not_called()
        self.assertFalse(hasattr(order, "invoice"))

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.tasks.send_invoice_to_mydata.delay")
    @patch("order.tasks.send_invoice_email.delay")
    def test_routes_to_mydata_when_enabled(
        self, mock_email, mock_submit, _mock_render
    ):
        self.enterContext(mydata_enabled())
        order = self._shipped_order()
        result = generate_order_invoice(order.id)
        self.assertTrue(result)
        mock_submit.assert_called_once_with(order.id)
        # Email is chained from ``send_invoice_to_mydata``, not
        # directly from ``generate_order_invoice``.
        mock_email.assert_not_called()

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.tasks.send_invoice_to_mydata.delay")
    def test_an_invoice_order_gets_no_document(self, mock_submit, mock_render):
        """Invoices are issued by hand in timologio (AADE decision
        Α.1128/2025): whoever asks, the platform builds nothing."""
        from order.enum.document_type import OrderDocumentTypeEnum

        self.enterContext(mydata_enabled())
        order = self._shipped_order(
            document_type=OrderDocumentTypeEnum.INVOICE,
            billing_vat_id="123456789",
        )
        self.assertFalse(generate_order_invoice(order.id))
        mock_submit.assert_not_called()
        mock_render.assert_not_called()
        self.assertFalse(Invoice.objects.filter(order=order).exists())

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.tasks.send_invoice_to_mydata.delay")
    def test_an_order_that_has_not_shipped_gets_no_receipt(
        self, mock_submit, mock_render
    ):
        self.enterContext(mydata_enabled())
        for status in (
            OrderStatus.PENDING,
            OrderStatus.PROCESSING,
            OrderStatus.CANCELED,
        ):
            order = self._shipped_order(status=status)
            self.assertFalse(generate_order_invoice(order.id))
        mock_submit.assert_not_called()
        self.assertFalse(Invoice.objects.exists())

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.tasks.send_invoice_to_mydata.delay")
    def test_a_registered_receipt_is_not_issued_again(
        self, mock_submit, _mock_render
    ):
        self.enterContext(mydata_enabled())
        order = self._shipped_order()
        generate_order_invoice(order.id)
        Invoice.objects.filter(order=order).update(mydata_mark=1)
        mock_submit.reset_mock()

        self.assertTrue(generate_order_invoice(order.id))

        mock_submit.assert_not_called()
        self.assertEqual(Invoice.objects.filter(order=order).count(), 1)


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
    def test_already_confirmed_invoice_short_circuits(
        self, mock_send, _mock_render
    ):
        """An invoice that already has a MARK must NOT be re-submitted —
        a re-submit could fail and flip CONFIRMED→REJECTED. submit_document
        returns a synthetic Success without calling AADE (G0262)."""
        from order.mydata.service import submit_document

        self.enterContext(mydata_enabled())
        _order, invoice = _make_invoice()
        invoice.mydata_mark = 800000165789545
        invoice.mydata_uid = "ffeeddccbbaa99887766554433221100ffeeddcc"
        invoice.save(update_fields=["mydata_mark", "mydata_uid"])

        row = submit_document(invoice)

        self.assertTrue(row.is_success)
        self.assertEqual(row.invoice_mark, 800000165789545)
        mock_send.assert_not_called()

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.issue_pending_credits.delay")
    @patch("order.tasks.send_invoice_email.delay")
    def test_success_persists_mark_and_chains_email(
        self, mock_email, mock_credits, mock_send, _mock_render
    ):
        """Happy path: AADE returns Success → MARK + qr_url persisted,
        PDF regenerated (so customer's copy carries the MARK), email
        queued as the last step in the chain."""
        self.enterContext(mydata_enabled())
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
        # Credit documents that waited for this MARK are released.
        mock_credits.assert_called_once_with(order.id)

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.send_invoice_email.delay")
    def test_a_refused_receipt_is_not_emailed(
        self, mock_email, mock_send, _mock_render
    ):
        """A receipt AADE refused (e.g. 216 bad vatCategory) is not
        issued, so the customer is not sent one: staff see the REJECTED
        row and the note on the order, fix it and resubmit."""
        self.enterContext(mydata_enabled())
        order, invoice = _make_invoice()
        mock_send.return_value = _VALIDATION_ERROR_XML

        result = send_invoice_to_mydata(order.id)

        self.assertFalse(result)
        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.REJECTED)
        self.assertEqual(invoice.mydata_error_code, "216")
        mock_email.assert_not_called()

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.send_invoice_email.delay")
    def test_transport_error_retries_then_waits_for_staff(
        self, mock_email, mock_send, _mock_render
    ):
        """Transport errors retry automatically via Celery. Once
        retries are exhausted the receipt stays SUBMITTED for staff to
        resubmit; it is not emailed unregistered."""
        self.enterContext(mydata_enabled())
        order, invoice = _make_invoice()
        mock_send.side_effect = MyDataTransportError("upstream 503")

        # Force "last retry reached" state so the task reaches the
        # terminal fallback branch rather than scheduling another retry.
        with patch("order.tasks.send_invoice_to_mydata.max_retries", 0):
            result = send_invoice_to_mydata(order.id)

        self.assertFalse(result)
        mock_email.assert_not_called()
        # Status stays SUBMITTED (not REJECTED) because transport
        # errors are recoverable — the row stays eligible for manual
        # resubmission via the admin action.
        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.SUBMITTED)

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.send_invoice_email.delay")
    def test_technical_error_is_retryable_not_terminal(
        self, mock_email, mock_send, _mock_render
    ):
        """An AADE ``TechnicalError`` row (gateway fault, incl. the
        empty-200 body the parser synthesises) is a transport fault, not a
        terminal rejection: the invoice must stay SUBMITTED and remain
        eligible for retry/manual resubmit — never flipped to REJECTED
        (G0260)."""
        self.enterContext(mydata_enabled())
        order, invoice = _make_invoice()
        mock_send.return_value = _TECHNICAL_ERROR_XML

        # Force the last-retry state so the task reaches the transport
        # fallback branch rather than scheduling another retry.
        with patch("order.tasks.send_invoice_to_mydata.max_retries", 0):
            result = send_invoice_to_mydata(order.id)

        self.assertFalse(result)
        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.SUBMITTED)
        self.assertNotEqual(invoice.mydata_status, MyDataStatus.REJECTED)
        mock_email.assert_not_called()

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.send_invoice_email.delay")
    def test_duplicate_uid_is_terminal_not_retried(
        self, mock_email, mock_send, _mock_render
    ):
        """Regression: AADE error 228 (duplicate uid) used to be
        caught in the retry branch alongside transport errors — that
        produced 5 wasted round-trips with the same uid (AADE
        answers 228 every time because the content is identical).
        Now the task treats it as terminal and logs the uid for manual
        reconciliation."""
        self.enterContext(mydata_enabled())
        order, invoice = _make_invoice()
        duplicate_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<ResponseDoc>
    <response>
        <index>1</index>
        <statusCode>ValidationError</statusCode>
        <errors>
            <code>228</code>
            <message>It has already been sent for another invoice (MARK: 123)</message>
        </errors>
    </response>
</ResponseDoc>"""
        mock_send.return_value = duplicate_xml

        result = send_invoice_to_mydata(order.id)

        # Must NOT retry — a single call is enough.
        self.assertFalse(result)
        self.assertEqual(mock_send.call_count, 1)
        mock_email.assert_not_called()
        # Invoice ends up in REJECTED state so ops can reconcile.
        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.REJECTED)
        self.assertEqual(invoice.mydata_error_code, "228")

    def test_missing_invoice_returns_false(self):
        """An order without a receipt row has nothing to submit and
        nothing to email."""
        self.enterContext(mydata_enabled())
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
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
        mock_email.assert_not_called()


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
    @patch("order.tasks.issue_pending_credits.delay")
    @patch("order.tasks.send_invoice_email.delay")
    def test_persists_cancellation_mark_on_success(
        self, _mock_email, _mock_credits, mock_cancel, mock_send, _mock_render
    ):
        self.enterContext(mydata_enabled())
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
        self.enterContext(mydata_enabled())
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        OrderItemFactory(
            order=order,
            product=ProductFactory(vat=VatFactory(value=24)),
            price=Money(Decimal("5.00"), "EUR"),
            quantity=1,
        )
        # No submission — invoice has no MARK.
        with patch("order.invoicing._render_pdf_bytes", return_value=b"x"):
            generate_invoice(issuable(order))

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
        self.enterContext(mydata_enabled())
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
            from order.enum.document_type import OrderDocumentTypeEnum

            vat = VatFactory(value=24)
            product = ProductFactory(vat=vat)
            order = OrderFactory(
                num_order_items=0,
                document_type=OrderDocumentTypeEnum.RECEIPT,
                status=OrderStatus.PENDING,
                payment_status=PaymentStatus.PENDING,
            )
            OrderItemFactory(
                order=order,
                product=product,
                price=Money(Decimal("12.40"), "EUR"),
                quantity=1,
            )
            generate_invoice(issuable(order))
            # Clear any emails the order_created signal already sent
            # (confirmation email runs eagerly under
            # CELERY_TASK_ALWAYS_EAGER + on_commit-immediate fixture).
            # Only the invoice email is under test here.
            mail.outbox = []
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
