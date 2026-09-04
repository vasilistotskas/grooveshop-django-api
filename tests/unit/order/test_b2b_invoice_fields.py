"""Company-requisite validation on INVOICE orders.

Covers the checksum on ``billing_vat_id``, the company identity trio an
INVOICE order must carry, and the same-as-shipping billing-address
snapshot copy.
"""

from unittest.mock import patch

from django.test import TestCase
from extra_settings.models import Setting

from order.serializers.order import OrderCreateFromCartSerializer

BASE_PAYLOAD = {
    "pay_way_id": 1,
    "first_name": "Nikos",
    "last_name": "Papadopoulos",
    "email": "nikos@example.com",
    "street": "Ermou",
    "street_number": "10",
    "city": "Athens",
    "zipcode": "10563",
    "country_id": "GR",
    "phone": "+302101234567",
}

INVOICE_PAYLOAD = {
    **BASE_PAYLOAD,
    "document_type": "INVOICE",
    "billing_vat_id": "123456783",
}

COMPANY_FIELDS = {
    "billing_company_name": "Example IKE",
    "billing_tax_office": "Α' Αθηνών",
    "billing_activity": "Retail trade",
}


class B2BInvoiceFieldsTestCase(TestCase):
    def _stub_settings(self, **values) -> None:
        def stub(cls, key, default=None):
            return values.get(key, default)

        p = patch.object(Setting, "get", classmethod(stub))
        p.start()
        self.addCleanup(p.stop)

    def test_checksum_rejects_format_valid_garbage(self):
        self._stub_settings(B2B_INVOICING_ENABLED=True)
        serializer = OrderCreateFromCartSerializer(
            data={**INVOICE_PAYLOAD, "billing_vat_id": "123456789"}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("billing_vat_id", serializer.errors)

    def test_company_trio_is_required_on_an_invoice(self):
        """A Greek invoice must name the counterparty."""
        self._stub_settings(B2B_INVOICING_ENABLED=True)
        serializer = OrderCreateFromCartSerializer(data=INVOICE_PAYLOAD)
        self.assertFalse(serializer.is_valid())
        for field in COMPANY_FIELDS:
            self.assertIn(field, serializer.errors)

    def test_company_trio_satisfies_the_invoice_requirement(self):
        self._stub_settings(B2B_INVOICING_ENABLED=True)
        serializer = OrderCreateFromCartSerializer(
            data={**INVOICE_PAYLOAD, **COMPANY_FIELDS}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_blank_billing_address_copies_shipping(self):
        self._stub_settings(B2B_INVOICING_ENABLED=True)
        serializer = OrderCreateFromCartSerializer(
            data={**INVOICE_PAYLOAD, **COMPANY_FIELDS}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["billing_street"], "Ermou")
        self.assertEqual(
            serializer.validated_data["billing_street_number"], "10"
        )
        self.assertEqual(serializer.validated_data["billing_city"], "Athens")
        self.assertEqual(serializer.validated_data["billing_zipcode"], "10563")

    def test_explicit_billing_address_not_overwritten(self):
        self._stub_settings(B2B_INVOICING_ENABLED=True)
        serializer = OrderCreateFromCartSerializer(
            data={
                **INVOICE_PAYLOAD,
                **COMPANY_FIELDS,
                "billing_street": "Stadiou",
                "billing_street_number": "5",
                "billing_city": "Piraeus",
                "billing_zipcode": "18531",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["billing_street"], "Stadiou")
        self.assertEqual(serializer.validated_data["billing_city"], "Piraeus")

    def test_receipt_never_requires_company_fields(self):
        self._stub_settings(B2B_INVOICING_ENABLED=True)
        serializer = OrderCreateFromCartSerializer(
            data={**BASE_PAYLOAD, "document_type": "RECEIPT"}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        # RECEIPT orders don't copy the shipping address into billing.
        self.assertEqual(
            serializer.validated_data.get("billing_street", ""), ""
        )

    def test_receipt_blanks_smuggled_billing_fields(self):
        """A retail receipt carries NO buyer tax identity — a stale or
        hand-crafted client sending billing fields with RECEIPT must
        not put a company block on the order (or the PDF, which
        branches on billing_company_name)."""
        self._stub_settings(B2B_INVOICING_ENABLED=True)
        serializer = OrderCreateFromCartSerializer(
            data={
                **BASE_PAYLOAD,
                "document_type": "RECEIPT",
                "billing_vat_id": "123456783",
                **COMPANY_FIELDS,
                "billing_street": "Stadiou",
                "billing_city": "Piraeus",
                "billing_zipcode": "18531",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        for field in (
            "billing_vat_id",
            "billing_country",
            "billing_company_name",
            "billing_tax_office",
            "billing_activity",
            "billing_street",
            "billing_street_number",
            "billing_city",
            "billing_zipcode",
        ):
            self.assertEqual(serializer.validated_data.get(field), "", field)

    def test_partial_explicit_billing_address_rejected(self):
        """billing_street without city/zipcode would render an invoice
        mixing the billing street with the SHIPPING city."""
        self._stub_settings(B2B_INVOICING_ENABLED=True)
        serializer = OrderCreateFromCartSerializer(
            data={
                **INVOICE_PAYLOAD,
                **COMPANY_FIELDS,
                "billing_street": "Stadiou",
                # city + zipcode deliberately missing
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("billing_city", serializer.errors)
        self.assertIn("billing_zipcode", serializer.errors)
