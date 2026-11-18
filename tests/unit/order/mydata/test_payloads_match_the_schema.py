"""Every payload the platform sends to myDATA matches AADE's v2.0.2 schema.

The XSDs are vendored (``order/accounting/mydata/xsd/``), so
``validate_invoice_doc`` runs before every ``SendInvoices``. This builds each
document the platform issues and validates it the same way: the plain
receipt, the receipt with movement indication, that receipt sent under the
loss-of-connection procedure, and the retail credit document.
``CancelInvoice`` sends no body (myDATA REST API v2.0.2, §4.2.5): its MARK
travels on the query string, so there is no payload to validate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone
from djmoney.money import Money

from order.accounting.mydata.builder import build_credit_xml
from order.accounting.mydata.validator import validate_invoice_doc
from order.models import RetailCredit
from order.models.retail_credit import CreditReason
from tests.unit.order.mydata.test_builder_movement import (
    MOVEMENT,
    _build,
    _receipt,
)

pytestmark = pytest.mark.django_db


def test_the_plain_receipt():
    validate_invoice_doc(_build(_receipt()).xml_bytes)


def test_the_receipt_with_movement_indication():
    validate_invoice_doc(_build(_receipt(), MOVEMENT).xml_bytes)


def test_the_receipt_sent_under_the_loss_of_connection_procedure():
    invoice = _receipt()
    invoice.transmission_failed_at = timezone.now()

    validate_invoice_doc(_build(invoice, MOVEMENT).xml_bytes)


def test_the_retail_credit_document():
    invoice = _receipt()
    credit = RetailCredit(
        order=invoice.order,
        invoice=invoice,
        reason=CreditReason.SETTLEMENT,
        credit_number="CRD-2026-000747",
        vat_breakdown=[
            {
                "rate": "24",
                "subtotal": "10.00",
                "vat": "2.40",
                "gross": "12.40",
            }
        ],
        subtotal=Money(Decimal("10.00"), "EUR"),
        total_vat=Money(Decimal("2.40"), "EUR"),
        total=Money(Decimal("12.40"), "EUR"),
    )

    built = build_credit_xml(
        credit,
        issuer_vat="123456789",
        issuer_country="GR",
        branch=0,
        series_prefix="GRVC",
    )

    validate_invoice_doc(built.xml_bytes)


def test_a_payload_off_the_schema_is_refused():
    from xmlschema import XMLSchemaValidationError

    xml = _build(_receipt(), MOVEMENT).xml_bytes.replace(
        b"<movePurpose>1</movePurpose>", b"<movePurpose>99</movePurpose>"
    )

    with pytest.raises(XMLSchemaValidationError):
        validate_invoice_doc(xml)
