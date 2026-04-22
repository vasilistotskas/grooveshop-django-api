"""Unit tests for :func:`order.mydata.builder.build_invoice_xml`.

Focuses on the mapping from ``Invoice`` → ``InvoicesDoc`` XML. Uses
the real ``OrderFactory`` / ``InvoiceCounter`` path to produce a
persisted invoice row, then parses the output XML back and asserts
structural invariants (element presence, line-sum consistency).
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
        self.assertEqual(root.tag, "InvoicesDoc")
        self.assertEqual(len(root.findall("invoice")), 1)

    def test_issuer_fields_are_populated(self):
        built = self._build(self._issued_invoice())
        root = fromstring(built.xml_bytes)
        issuer = root.find("invoice/issuer")
        self.assertIsNotNone(issuer)
        self.assertEqual(issuer.find("vatNumber").text, "123456789")
        self.assertEqual(issuer.find("country").text, "GR")
        self.assertEqual(issuer.find("branch").text, "0")

    def test_invoice_header_uses_tier_a_defaults(self):
        built = self._build(self._issued_invoice())
        root = fromstring(built.xml_bytes)
        header = root.find("invoice/invoiceHeader")
        self.assertIsNotNone(header)
        # Tier A: B2C retail only.
        self.assertEqual(header.find("invoiceType").text, "11.1")
        self.assertEqual(header.find("currency").text, "EUR")
        # Series: "{prefix}-{year}"
        self.assertTrue(header.find("series").text.startswith("GRVP-"))
        # aa is a plain integer — AADE rejects padded / prefixed strings.
        aa_text = header.find("aa").text
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
        totals MUST equal the sum of line items. Line-level netValue
        + vatAmount must equal gross after rounding."""
        built = self._build(self._issued_invoice(vat_rate=24))
        root = fromstring(built.xml_bytes)
        lines = root.findall("invoice/invoiceDetails")
        self.assertGreater(len(lines), 0)
        total_net = Decimal("0")
        total_vat = Decimal("0")
        for row in lines:
            net = Decimal(row.find("netValue").text)
            vat = Decimal(row.find("vatAmount").text)
            total_net += net
            total_vat += vat

        summary = root.find("invoice/invoiceSummary")
        summary_net = Decimal(summary.find("totalNetValue").text)
        summary_vat = Decimal(summary.find("totalVatAmount").text)
        self.assertEqual(total_net, summary_net)
        self.assertEqual(total_vat, summary_vat)

    def test_payment_method_row_amount_matches_total(self):
        built = self._build(self._issued_invoice())
        root = fromstring(built.xml_bytes)
        pay_amount = root.find(
            "invoice/paymentMethods/paymentMethodDetails/amount"
        ).text
        summary_total = root.find("invoice/invoiceSummary/totalGrossValue").text
        self.assertEqual(pay_amount, summary_total)

    def test_xml_has_utf8_declaration(self):
        built = self._build(self._issued_invoice())
        self.assertTrue(built.xml_bytes.startswith(b"<?xml"))
        self.assertIn(b"utf-8", built.xml_bytes[:100].lower())
