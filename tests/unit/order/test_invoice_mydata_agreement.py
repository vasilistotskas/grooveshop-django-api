"""The PDF invoice and the myDATA payload report the same money.

Both documents derive their lines from ``order.taxation``, so for ANY
order the ``Invoice`` row (and the VAT table printed from it) must equal
the payload's ``invoiceSummary`` to the cent: net, VAT and gross. Before,
the PDF added shipping and the payment fee on top with no VAT while
myDATA taxed them at a flat 24%, and the two rounded differently, so the
document the customer holds and the one filed with AADE disagreed.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

import pytest
from djmoney.money import Money

from order.factories.order import OrderFactory
from order.invoicing import generate_invoice
from order.mydata.builder import build_invoice_xml
from order.mydata.types import (
    VAT_CATEGORY_0,
    VAT_CATEGORY_4,
    VAT_CATEGORY_6,
    VAT_CATEGORY_13,
    VAT_CATEGORY_24,
)
from product.factories import ProductFactory
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

D = Decimal
NS = "{http://www.aade.gr/myDATA/invoice/v1.0}"
CATEGORY_BY_RATE = {
    D("24"): VAT_CATEGORY_24,
    D("13"): VAT_CATEGORY_13,
    D("6"): VAT_CATEGORY_6,
    D("4"): VAT_CATEGORY_4,
    D("0"): VAT_CATEGORY_0,
}


def _order(lines, *, shipping, fee, discount=None, loyalty=None):
    """``lines``: ``(unit gross, VAT rate, quantity)`` per goods line."""
    order = OrderFactory(
        num_order_items=0,
        shipping_price=Money(D(shipping), "EUR"),
        payment_method_fee=Money(D(fee), "EUR"),
    )
    order.items.all().delete()
    for gross, rate, quantity in lines:
        order.items.create(
            product=ProductFactory(vat=VatFactory(value=D(rate))),
            price=Money(D(gross), "EUR"),
            quantity=quantity,
            vat_rate=D(rate),
        )
    if discount is not None:
        order.discount_amount = Money(D(discount), "EUR")
    if loyalty is not None:
        order.loyalty_discount = Money(D(loyalty), "EUR")
    order.save()
    return order


def _documents(order):
    """The persisted ``Invoice`` and the myDATA payload built from it."""
    with patch(
        "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test"
    ):
        invoice = generate_invoice(order)
    built = build_invoice_xml(
        invoice,
        issuer_vat="123456789",
        issuer_country="GR",
        branch=0,
        series_prefix="GRVP",
    )
    return invoice, fromstring(built.xml_bytes)


def _summary(root, name):
    return D(root.find(f"{NS}invoice/{NS}invoiceSummary/{NS}{name}").text)


SCENARIOS = {
    # The issue's example: 3.50 shipping and a 1.25 fee.
    "shipping and fee on one 24% line": {
        "lines": [("12.40", "24", 2)],
        "shipping": "3.50",
        "fee": "1.25",
    },
    "mixed rates, shipping and fee": {
        "lines": [("124.00", "24", 1), ("113.00", "13", 1), ("10.60", "6", 3)],
        "shipping": "7.35",
        "fee": "2.10",
    },
    "mixed rates under a promotion and loyalty discount": {
        "lines": [("33.33", "24", 3), ("7.77", "13", 2), ("1.01", "6", 7)],
        "shipping": "4.99",
        "fee": "1.23",
        "discount": "9.99",
        "loyalty": "2.50",
    },
    # Rounding the summed bucket gives a different cent than summing the
    # rounded lines: 2.42/0.58 against 2.43/0.57.
    "three 1.00 lines, where bucket rounding drifts": {
        "lines": [("1.00", "24", 1)] * 3,
        "shipping": "0",
        "fee": "0",
    },
    # 0.13 at 4% has a net of exactly 0.125: rounding net and VAT
    # independently billed 0.28 for 0.26 of goods.
    "two 0.13 lines at 4%, a half-cent tie": {
        "lines": [("0.13", "4", 1)] * 2,
        "shipping": "0",
        "fee": "0",
    },
    "zero-rated goods": {
        "lines": [("10.00", "0", 1)],
        "shipping": "3.50",
        "fee": "1.25",
    },
    "shipping without a fee": {
        "lines": [("20.00", "13", 1)],
        "shipping": "3.50",
        "fee": "0",
    },
}


def _charged(scenario):
    """What the customer is charged: goods and extras, less discounts."""
    goods = sum(
        (D(gross) * qty for gross, _rate, qty in scenario["lines"]), D(0)
    )
    return (
        goods
        + D(scenario["shipping"])
        + D(scenario["fee"])
        - D(scenario.get("discount", 0))
        - D(scenario.get("loyalty", 0))
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=list(SCENARIOS))
def test_invoice_row_equals_the_mydata_summary_to_the_cent(scenario):
    invoice, root = _documents(_order(**SCENARIOS[scenario]))

    assert invoice.subtotal.amount == _summary(root, "totalNetValue")
    assert invoice.total_vat.amount == _summary(root, "totalVatAmount")
    assert invoice.total.amount == _summary(root, "totalGrossValue")
    assert invoice.subtotal.amount + invoice.total_vat.amount == (
        invoice.total.amount
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=list(SCENARIOS))
def test_both_documents_bill_exactly_what_the_customer_is_charged(scenario):
    invoice, root = _documents(_order(**SCENARIOS[scenario]))

    assert invoice.total.amount == _charged(SCENARIOS[scenario])
    assert _summary(root, "totalGrossValue") == _charged(SCENARIOS[scenario])


@pytest.mark.parametrize("scenario", SCENARIOS, ids=list(SCENARIOS))
def test_the_printed_vat_table_equals_the_mydata_lines_rate_by_rate(scenario):
    invoice, root = _documents(_order(**SCENARIOS[scenario]))

    filed: dict[int, list[Decimal]] = defaultdict(lambda: [D(0), D(0)])
    for row in root.findall(f"{NS}invoice/{NS}invoiceDetails"):
        category = int(row.find(f"{NS}vatCategory").text)
        filed[category][0] += D(row.find(f"{NS}netValue").text)
        filed[category][1] += D(row.find(f"{NS}vatAmount").text)

    printed = {
        CATEGORY_BY_RATE[D(row["rate"])]: [D(row["subtotal"]), D(row["vat"])]
        for row in invoice.vat_breakdown
    }
    assert printed == dict(filed)


def test_shipping_and_fee_vat_counts_in_the_invoice_total_vat():
    """The issue's example, with the figures spelled out: goods 24.80
    (20.00 + 4.80), shipping 3.50 (2.82 + 0.68), fee 1.25 (1.01 + 0.24).
    The row used to keep 4.80 while myDATA filed 5.72."""
    invoice, root = _documents(
        _order([("12.40", "24", 2)], shipping="3.50", fee="1.25")
    )

    assert invoice.total_vat.amount == D("5.72")
    assert invoice.subtotal.amount == D("23.83")
    assert invoice.total.amount == D("29.55")
    assert _summary(root, "totalVatAmount") == D("5.72")
    assert _summary(root, "totalNetValue") == D("23.83")
    assert _summary(root, "totalGrossValue") == D("29.55")
