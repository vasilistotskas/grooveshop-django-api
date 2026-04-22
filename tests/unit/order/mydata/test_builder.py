"""Unit tests for :func:`order.mydata.builder.build_invoice_xml`.

Focuses on the mapping from ``Invoice`` → ``InvoicesDoc`` XML. Uses
the real ``OrderFactory`` / ``InvoiceCounter`` path to produce a
persisted invoice row, then parses the output XML back and asserts
structural invariants (element presence, line-sum consistency).

The emitted XML sits under the AADE namespace
``http://www.aade.gr/myDATA/invoice/v1.0`` (mandatory — without it
AADE returns error 101 for every element). The helper
``_find_el`` / ``_find_all`` prepend the namespace so the test bodies
stay readable.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from django.test import TestCase
from djmoney.money import Money

from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.invoicing import generate_invoice
from order.mydata.builder import build_invoice_xml
from product.factories.product import ProductFactory
from vat.factories import VatFactory

AADE_NS = "{http://www.aade.gr/myDATA/invoice/v1.0}"


def _localise(path: str) -> str:
    """Prepend the AADE namespace to every segment of a ``/``-joined
    XPath. Keeps the readable short form in tests."""
    return "/".join(f"{AADE_NS}{part}" for part in path.split("/"))


class BuildInvoiceXmlTestCase(TestCase):
    def _issued_invoice(self, vat_rate: int = 24):
        vat = VatFactory(value=vat_rate)
        product = ProductFactory(vat=vat)
        order = OrderFactory(num_order_items=0)
        OrderItemFactory(
            order=order,
            product=product,
            price=Money(Decimal("12.40"), "EUR"),
            quantity=2,
        )
        with patch(
            "order.invoicing._render_pdf_bytes",
            return_value=b"%PDF-1.4 test",
        ):
            return generate_invoice(order)

    def _build(self, invoice):
        return build_invoice_xml(
            invoice,
            issuer_vat="123456789",
            issuer_country="GR",
            branch=0,
            series_prefix="GRVP",
        )

    def test_returns_invoices_doc_root(self):
        built = self._build(self._issued_invoice())
        root = fromstring(built.xml_bytes)
        # Root tag in Clark notation includes the namespace.
        self.assertEqual(root.tag, f"{AADE_NS}InvoicesDoc")
        self.assertEqual(len(root.findall(f"{AADE_NS}invoice")), 1)

    def test_issuer_fields_are_populated(self):
        built = self._build(self._issued_invoice())
        root = fromstring(built.xml_bytes)
        issuer = root.find(_localise("invoice/issuer"))
        self.assertIsNotNone(issuer)
        self.assertEqual(issuer.find(f"{AADE_NS}vatNumber").text, "123456789")
        self.assertEqual(issuer.find(f"{AADE_NS}country").text, "GR")
        self.assertEqual(issuer.find(f"{AADE_NS}branch").text, "0")

    def test_invoice_header_uses_tier_a_defaults(self):
        built = self._build(self._issued_invoice())
        root = fromstring(built.xml_bytes)
        header = root.find(_localise("invoice/invoiceHeader"))
        self.assertIsNotNone(header)
        self.assertEqual(header.find(f"{AADE_NS}invoiceType").text, "11.1")
        self.assertEqual(header.find(f"{AADE_NS}currency").text, "EUR")
        self.assertTrue(
            header.find(f"{AADE_NS}series").text.startswith("GRVP-")
        )
        aa_text = header.find(f"{AADE_NS}aa").text
        self.assertEqual(aa_text, str(int(aa_text)))

    def test_uid_is_attached_and_deterministic(self):
        """Building the same invoice twice must yield the same uid —
        idempotency baseline for Celery retries."""
        invoice = self._issued_invoice()
        first = self._build(invoice)
        second = self._build(invoice)
        self.assertEqual(len(first.uid), 40)
        self.assertEqual(first.uid, second.uid)

    def test_line_items_net_plus_vat_equals_gross(self):
        """Per AADE v1.0.10 (error codes 203, 207–210), the summary
        totals MUST equal the sum of line items."""
        built = self._build(self._issued_invoice(vat_rate=24))
        root = fromstring(built.xml_bytes)
        lines = root.findall(_localise("invoice/invoiceDetails"))
        self.assertGreater(len(lines), 0)
        total_net = Decimal("0")
        total_vat = Decimal("0")
        for row in lines:
            total_net += Decimal(row.find(f"{AADE_NS}netValue").text)
            total_vat += Decimal(row.find(f"{AADE_NS}vatAmount").text)

        summary = root.find(_localise("invoice/invoiceSummary"))
        self.assertEqual(
            total_net,
            Decimal(summary.find(f"{AADE_NS}totalNetValue").text),
        )
        self.assertEqual(
            total_vat,
            Decimal(summary.find(f"{AADE_NS}totalVatAmount").text),
        )

    def test_payment_method_row_amount_matches_total(self):
        built = self._build(self._issued_invoice())
        root = fromstring(built.xml_bytes)
        pay_amount = root.find(
            _localise("invoice/paymentMethods/paymentMethodDetails/amount")
        ).text
        summary_total = root.find(
            _localise("invoice/invoiceSummary/totalGrossValue")
        ).text
        self.assertEqual(pay_amount, summary_total)

    def test_xml_has_utf8_declaration(self):
        built = self._build(self._issued_invoice())
        self.assertTrue(built.xml_bytes.startswith(b"<?xml"))
        self.assertIn(b"utf-8", built.xml_bytes[:100].lower())

    def test_invoice_header_precedes_payment_methods(self):
        """Regression: AADE XSD (verified via live dev response — the
        PDF docs are misleading on ordering): ``issuer, counterpart,
        invoiceHeader, paymentMethods, invoiceDetails, taxesTotals,
        invoiceSummary``. Pin the live-verified order so a future
        refactor that trusts the docs over the wire breaks loudly."""
        built = self._build(self._issued_invoice())
        root = fromstring(built.xml_bytes)
        invoice_el = root.find(f"{AADE_NS}invoice")
        self.assertIsNotNone(invoice_el)
        # Strip namespace for an easy-to-read index-based assertion.
        children = [c.tag.split("}", 1)[-1] for c in invoice_el]
        self.assertLess(
            children.index("invoiceHeader"),
            children.index("paymentMethods"),
        )

    def test_aade_default_namespace_present(self):
        """Regression: without ``xmlns="http://www.aade.gr/myDATA/
        invoice/v1.0"`` on the root, AADE returns error 101 "Could
        not find schema information" for every element."""
        built = self._build(self._issued_invoice())
        self.assertIn(
            b'xmlns="http://www.aade.gr/myDATA/invoice/v1.0"',
            built.xml_bytes,
        )

    def test_income_classification_present_on_every_detail_row(self):
        """Regression: AADE error 314 — every invoice line must carry
        either incomeClassification or expensesClassification."""
        built = self._build(self._issued_invoice())
        root = fromstring(built.xml_bytes)
        for row in root.findall(_localise("invoice/invoiceDetails")):
            cls = row.find(f"{AADE_NS}incomeClassification")
            self.assertIsNotNone(cls, "invoiceDetails missing classification")
            # AADE dev verified these are the correct values for
            # invoiceType 11.1 (retail receipt / Ιδιωτική Πελατεία).
            # 001/category1_1 is wholesale → error 313 on 11.1.
            cls_ns = "{https://www.aade.gr/myDATA/incomeClassificaton/v1.0}"
            self.assertEqual(
                cls.find(f"{cls_ns}classificationType").text,
                "E3_561_003",
            )
            self.assertEqual(
                cls.find(f"{cls_ns}classificationCategory").text,
                "category1_3",
            )
            self.assertIsNotNone(cls.find(f"{cls_ns}amount").text)

    def test_income_classification_aggregated_in_summary(self):
        """Summary incomeClassification must mirror per-line sums by
        (classificationType, classificationCategory). Classification
        inner elements live in a separate AADE namespace."""
        cls_ns = "{https://www.aade.gr/myDATA/incomeClassificaton/v1.0}"
        built = self._build(self._issued_invoice())
        root = fromstring(built.xml_bytes)
        line_total = Decimal("0")
        for row in root.findall(_localise("invoice/invoiceDetails")):
            amt = row.find(f"{AADE_NS}incomeClassification/{cls_ns}amount").text
            line_total += Decimal(amt)
        summary_entries = root.findall(
            _localise("invoice/invoiceSummary/incomeClassification")
        )
        self.assertEqual(len(summary_entries), 1)
        summary_amount = Decimal(
            summary_entries[0].find(f"{cls_ns}amount").text
        )
        self.assertEqual(line_total, summary_amount)

    def test_payment_type_card_online_is_pos_code(self):
        """Regression: payment codes in types.py were shifted by 1.
        Online-paid orders (payment_id set) must emit type=7
        (POS / e-POS), NOT type=3 (cash)."""
        invoice = self._issued_invoice()
        invoice.order.payment_id = "stripe_ch_abc123"
        invoice.order.save(update_fields=["payment_id"])
        built = self._build(invoice)
        root = fromstring(built.xml_bytes)
        pay_type = root.find(
            _localise("invoice/paymentMethods/paymentMethodDetails/type")
        ).text
        self.assertEqual(pay_type, "7")

    def test_payment_type_cod_is_cash_code(self):
        """Regression: COD orders (no payment_id, no online pay_way)
        must emit type=3 (cash) — not type=7 which would claim
        a card capture happened."""
        invoice = self._issued_invoice()
        invoice.order.payment_id = ""
        invoice.order.save(update_fields=["payment_id"])
        if invoice.order.pay_way is not None:
            invoice.order.pay_way.is_online_payment = False
            invoice.order.pay_way.save(update_fields=["is_online_payment"])
        built = self._build(invoice)
        root = fromstring(built.xml_bytes)
        pay_type = root.find(
            _localise("invoice/paymentMethods/paymentMethodDetails/type")
        ).text
        self.assertEqual(pay_type, "3")

    def test_unknown_vat_rate_raises_not_silently_remapped(self):
        """Regression: silent fallback to category 7 (0%) on unknown
        rates used to trigger AADE error 217 with a misleading
        message. Now we raise a builder-side ValueError so
        ``submit_invoice`` converts it to MyDataValidationError."""
        from order.mydata.builder import _vat_category

        with self.assertRaises(ValueError):
            _vat_category(Decimal("11"))

    def test_zero_rate_emits_vat_exemption_category(self):
        """AADE error 217: vatCategory=7 (0%) requires
        ``vatExemptionCategory``."""
        vat = VatFactory(value=0)
        product = ProductFactory(vat=vat)
        order = OrderFactory(num_order_items=0)
        OrderItemFactory(
            order=order,
            product=product,
            price=Money(Decimal("5.00"), "EUR"),
            quantity=1,
        )
        with patch(
            "order.invoicing._render_pdf_bytes",
            return_value=b"%PDF-1.4 test",
        ):
            invoice = generate_invoice(order)
        built = self._build(invoice)
        root = fromstring(built.xml_bytes)
        zero_rows = [
            row
            for row in root.findall(_localise("invoice/invoiceDetails"))
            if row.find(f"{AADE_NS}vatCategory").text == "7"
        ]
        self.assertEqual(len(zero_rows), 1)
        self.assertEqual(
            zero_rows[0].find(f"{AADE_NS}vatExemptionCategory").text, "30"
        )

    def test_shipping_and_payment_fee_emitted_as_detail_lines(self):
        """Regression: shipping + payment_method_fee contribute to
        invoice.total but were NOT emitted as invoiceDetails rows,
        so paymentMethods.amount didn't match invoiceSummary.
        totalGrossValue and AADE rejected the submission."""
        invoice = self._issued_invoice()
        self.assertGreater(invoice.order.shipping_price.amount, 0)
        built = self._build(invoice)
        root = fromstring(built.xml_bytes)
        rows = root.findall(_localise("invoice/invoiceDetails"))
        self.assertGreaterEqual(len(rows), 2)
        pay_amount = Decimal(
            root.find(
                _localise("invoice/paymentMethods/paymentMethodDetails/amount")
            ).text
        )
        summary_gross = Decimal(
            root.find(_localise("invoice/invoiceSummary/totalGrossValue")).text
        )
        self.assertEqual(pay_amount, summary_gross)
