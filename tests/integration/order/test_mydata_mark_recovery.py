"""Integration tests for the Tier A.5 MARK recovery path.

When AADE returns error 228 (ERROR_DUPLICATE_UID) during
``send_invoice_to_mydata``, the service layer calls
``recover_mark_for_invoice`` which queries ``RequestTransmittedDocs``
to find the MARK that AADE already assigned to our uid. These tests
verify the four key scenarios:

1. 228 + exactly one matching uid with a MARK → CONFIRMED.
2. 228 + zero matches → REJECTED (no MARK written).
3. 228 + multiple matches for the uid → REJECTED (safety guard).
4. 228 + recovery query transport error → REJECTED, no crash.

HTTP layer is mocked at ``MyDataClient`` — no real AADE calls.

Design note: ``submit_invoice`` writes the invoice's uid via
``_persist_submission_intent`` (a deterministic SHA-1) BEFORE calling
``send_invoices``. The ``request_transmitted_docs`` mock therefore
receives a call referencing ``invoice.mydata_uid`` — we capture that
uid by inspecting the invoice after the call to build a matching
``RequestedDoc`` XML dynamically.  We use a ``side_effect`` function
on the ``request_transmitted_docs`` mock so it can read the invoice
uid at call time rather than at fixture-construction time.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from djmoney.money import Money

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.invoicing import generate_invoice
from order.models.invoice import MyDataStatus
from order.mydata.config import MyDataConfig
from order.mydata.exceptions import MyDataTransportError
from order.tasks import send_invoice_to_mydata
from product.factories.product import ProductFactory
from vat.factories import VatFactory

_READY_CONFIG = MyDataConfig(
    enabled=True,
    auto_submit=True,
    environment="dev",
    user_id="test-user",
    subscription_key="test-subscription-key",
    invoice_series_prefix="GRVP",
    issuer_branch=0,
    request_timeout_seconds=30,
)

_RECOVERED_MARK = 999000111

_DUPLICATE_228_XML = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<ResponseDoc>
    <response>
        <index>1</index>
        <statusCode>ValidationError</statusCode>
        <errors>
            <code>228</code>
            <message>It has already been sent (MARK: 999000111)</message>
        </errors>
    </response>
</ResponseDoc>"""

# Empty RequestedDoc — no docs returned
_TRANSMITTED_EMPTY = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<RequestedDoc>
    <invoicesDoc/>
</RequestedDoc>"""


def _make_transmitted_xml_with_uid(
    uid: str, *, duplicate: bool = False
) -> bytes:
    """Build a ``RequestedDoc`` XML containing invoice(s) with the given
    uid. When ``duplicate=True``, emit the same uid twice (different
    MARKs) to exercise the safety guard."""
    if duplicate:
        return (
            f"""<?xml version="1.0" encoding="UTF-8"?>
<RequestedDoc>
    <invoicesDoc>
        <invoice>
            <uid>{uid}</uid>
            <invoiceMark>{_RECOVERED_MARK}</invoiceMark>
        </invoice>
        <invoice>
            <uid>{uid}</uid>
            <invoiceMark>{_RECOVERED_MARK + 111}</invoiceMark>
        </invoice>
    </invoicesDoc>
</RequestedDoc>"""
        ).encode()
    return (
        f"""<?xml version="1.0" encoding="UTF-8"?>
<RequestedDoc>
    <invoicesDoc>
        <invoice>
            <uid>{uid}</uid>
            <invoiceMark>{_RECOVERED_MARK}</invoiceMark>
        </invoice>
    </invoicesDoc>
</RequestedDoc>"""
    ).encode()


def _enable_mydata(test_case: TestCase) -> None:
    """Patch the myDATA integration boundary (same helper as the main
    test module — see test_mydata_submission.py for rationale)."""
    for target, retval in (
        ("order.mydata.config.load_config", _READY_CONFIG),
        ("order.mydata.service.load_config", _READY_CONFIG),
        ("order.mydata.service._resolve_issuer_vat", "123456789"),
    ):
        p = patch(target, return_value=retval)
        p.start()
        test_case.addCleanup(p.stop)


def _make_invoice():
    """Create order + generate invoice (no uid pre-setting needed)."""
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
        invoice = generate_invoice(order)
    return order, invoice


@override_settings(
    SITE_NAME="GrooveShop",
    INFO_EMAIL="support@example.com",
    NUXT_BASE_URL="http://example.com",
    STATIC_BASE_URL="http://example.com",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
)
class MarkRecoveryTestCase(TestCase):
    """Tier A.5: MARK recovery via RequestTransmittedDocs."""

    # ------------------------------------------------------------------
    # Scenario 1: 228 + one matching uid with MARK → CONFIRMED
    # ------------------------------------------------------------------

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.send_invoice_email.delay")
    def test_228_recovery_success_confirms_invoice(
        self, mock_email, mock_send, _mock_render
    ):
        """When RequestTransmittedDocs returns exactly one doc matching
        our uid, the invoice must be flipped to CONFIRMED with the
        recovered MARK and the email chain must still fire."""
        _enable_mydata(self)
        order, invoice = _make_invoice()
        mock_send.return_value = _DUPLICATE_228_XML

        # The request_transmitted_docs mock is a side_effect function:
        # by the time it is called, submit_invoice has already run
        # _persist_submission_intent and written invoice.mydata_uid.
        # We read the uid from the DB here and return matching XML so
        # recover_mark_for_invoice can find the match.
        query_mock = MagicMock()

        def _query_side_effect(**kwargs):
            from order.models.invoice import Invoice as _Invoice

            real_uid = (
                _Invoice.objects.filter(pk=invoice.pk)
                .values_list("mydata_uid", flat=True)
                .first()
                or ""
            )
            return _make_transmitted_xml_with_uid(real_uid)

        query_mock.side_effect = _query_side_effect

        with patch(
            "order.mydata.client.MyDataClient.request_transmitted_docs",
            new=query_mock,
        ):
            result = send_invoice_to_mydata(order.id)

        # Task reports success (MARK recovered = treated as success).
        self.assertTrue(result)

        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.CONFIRMED)
        self.assertEqual(invoice.mydata_mark, _RECOVERED_MARK)

        # Email is still delivered — customer gets their invoice PDF.
        mock_email.assert_called_once_with(order.id)

        # RequestTransmittedDocs was called exactly once (single page).
        query_mock.assert_called_once()

    # ------------------------------------------------------------------
    # Scenario 2: 228 + zero matches → REJECTED
    # ------------------------------------------------------------------

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch(
        "order.mydata.client.MyDataClient.request_transmitted_docs",
        return_value=_TRANSMITTED_EMPTY,
    )
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.send_invoice_email.delay")
    def test_228_recovery_zero_matches_stays_rejected(
        self, mock_email, mock_send, _mock_query, _mock_render
    ):
        """When RequestTransmittedDocs returns no docs matching our uid,
        the invoice must stay REJECTED and no MARK must be written."""
        _enable_mydata(self)
        order, invoice = _make_invoice()
        mock_send.return_value = _DUPLICATE_228_XML

        result = send_invoice_to_mydata(order.id)

        self.assertFalse(result)

        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.REJECTED)
        # MARK must NOT have been written.
        self.assertIsNone(invoice.mydata_mark)
        self.assertEqual(invoice.mydata_error_code, "228")

        # Email still delivered.
        mock_email.assert_called_once_with(order.id)

    # ------------------------------------------------------------------
    # Scenario 3: 228 + multiple matches for the uid → REJECTED (safety)
    # ------------------------------------------------------------------

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.send_invoice_email.delay")
    def test_228_recovery_multiple_matches_safety_guard(
        self, mock_email, mock_send, _mock_render
    ):
        """When RequestTransmittedDocs returns more than one doc matching
        our uid, the safety guard must prevent writing any MARK and the
        invoice must stay REJECTED. Writing the wrong MARK would be
        worse than staying REJECTED."""
        _enable_mydata(self)
        order, invoice = _make_invoice()
        mock_send.return_value = _DUPLICATE_228_XML

        query_mock = MagicMock()

        def _query_side_effect(**kwargs):
            from order.models.invoice import Invoice as _Invoice

            real_uid = (
                _Invoice.objects.filter(pk=invoice.pk)
                .values_list("mydata_uid", flat=True)
                .first()
                or ""
            )
            # Two docs with the same uid — triggers the safety guard.
            return _make_transmitted_xml_with_uid(real_uid, duplicate=True)

        query_mock.side_effect = _query_side_effect

        with patch(
            "order.mydata.client.MyDataClient.request_transmitted_docs",
            new=query_mock,
        ):
            result = send_invoice_to_mydata(order.id)

        self.assertFalse(result)

        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.REJECTED)
        # MARK must NOT have been written (safety guard fired).
        self.assertIsNone(invoice.mydata_mark)
        self.assertEqual(invoice.mydata_error_code, "228")

        mock_email.assert_called_once_with(order.id)

    # ------------------------------------------------------------------
    # Scenario 4: 228 + recovery query transport error → REJECTED
    # ------------------------------------------------------------------

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @patch(
        "order.mydata.client.MyDataClient.request_transmitted_docs",
        side_effect=MyDataTransportError("network timeout"),
    )
    @patch("order.mydata.client.MyDataClient.send_invoices")
    @patch("order.tasks.send_invoice_email.delay")
    def test_228_recovery_transport_error_no_crash(
        self, mock_email, mock_send, mock_query, _mock_render
    ):
        """When the RequestTransmittedDocs call itself fails with a
        transport error, the task must NOT crash or retry — it must
        fall back cleanly to the REJECTED state and still deliver the
        email."""
        _enable_mydata(self)
        order, invoice = _make_invoice()
        mock_send.return_value = _DUPLICATE_228_XML

        # Must not raise.
        result = send_invoice_to_mydata(order.id)

        self.assertFalse(result)

        invoice.refresh_from_db()
        self.assertEqual(invoice.mydata_status, MyDataStatus.REJECTED)
        self.assertIsNone(invoice.mydata_mark)
        self.assertEqual(invoice.mydata_error_code, "228")

        # Email still delivered despite the transport error in recovery.
        mock_email.assert_called_once_with(order.id)

        # RequestTransmittedDocs was attempted exactly once (no recovery
        # retry — transport errors during recovery fall back immediately).
        mock_query.assert_called_once()
