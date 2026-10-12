"""The retail credit document (11.4) payload, from
:func:`order.mydata.builder.build_credit_xml`.

AADE's classification combinations sheet for 11.4 allows ``category1_1``
with ``E3_561_003``, like the receipt. AADE forbids ``correlatedInvoices`` on
an 11.4 (error 205, confirmed against the dev API), so the payload names no
receipt, and ``specialInvoiceCategory`` is not sent.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from xml.etree.ElementTree import fromstring

from djmoney.money import Money

from order.mydata.builder import build_credit_xml

AADE_NS = "{http://www.aade.gr/myDATA/invoice/v1.0}"
CLS_NS = "{https://www.aade.gr/myDATA/incomeClassificaton/v1.0}"
RECEIPT_MARK = 400000000000123


def _receipt(**overrides):
    order = SimpleNamespace(
        pk=1,
        items=SimpleNamespace(all=list),
        payment_id="pi_1",
        pay_way=SimpleNamespace(settlement="online"),
        gift_card_amount=Money(0, "EUR"),
    )
    values = {
        "order": order,
        "issue_date": date(2026, 10, 12),
        "mydata_series": "",
        "mydata_aa": None,
        "document_number": "INV-2026-000007",
        "invoice_number": "INV-2026-000007",
        "currency": "EUR",
        "mydata_mark": RECEIPT_MARK,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _credit(receipt, rows, *, total="50.00", **overrides):
    values = {
        "order": receipt.order,
        "invoice": receipt,
        "issue_date": date(2026, 10, 13),
        "mydata_series": "",
        "mydata_aa": None,
        "document_number": "CRD-2026-000003",
        "currency": "EUR",
        "total": Money(Decimal(total), "EUR"),
        "vat_breakdown": rows,
        "mydata_mark": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


ROWS = [
    {"rate": "24.00", "subtotal": "24.19", "vat": "5.81", "gross": "30.00"},
    {"rate": "13.00", "subtotal": "17.70", "vat": "2.30", "gross": "20.00"},
]


def _build(credit):
    built = build_credit_xml(
        credit,
        issuer_vat="123456789",
        issuer_country="GR",
        branch=0,
        series_prefix="GRVC",
    )
    return built, fromstring(built.xml_bytes)


def _text(root, path):
    el = root.find("/".join(f"{AADE_NS}{p}" for p in path.split("/")))
    return None if el is None else el.text


def test_it_is_a_retail_credit_that_names_no_receipt():
    built, root = _build(_credit(_receipt(), ROWS))

    assert built.invoice_type == "11.4"
    assert _text(root, "invoice/invoiceHeader/invoiceType") == "11.4"
    # AADE answers 205 to correlatedInvoices on this type.
    assert _text(root, "invoice/invoiceHeader/correlatedInvoices") is None
    assert b"correlatedInvoices" not in built.xml_bytes
    assert b"specialInvoiceCategory" not in built.xml_bytes
    # A retail document: no counterpart.
    assert root.find(f"{AADE_NS}invoice/{AADE_NS}counterpart") is None


def test_the_header_is_series_aa_date_type_currency_only():
    _built, root = _build(_credit(_receipt(), ROWS))
    header = root.find(f"{AADE_NS}invoice/{AADE_NS}invoiceHeader")

    tags = [child.tag.removeprefix(AADE_NS) for child in header]

    assert tags == ["series", "aa", "issueDate", "invoiceType", "currency"]


def test_it_has_its_own_series_and_the_aa_of_its_number():
    built, root = _build(_credit(_receipt(), ROWS))

    assert built.series == "GRVC-2026"
    assert built.aa == 3
    assert _text(root, "invoice/invoiceHeader/series") == "GRVC-2026"
    assert _text(root, "invoice/invoiceHeader/aa") == "3"


def test_goods_are_category_1_1_with_the_retail_private_customer_type():
    _built, root = _build(_credit(_receipt(), ROWS))
    rows = root.findall(f"{AADE_NS}invoice/{AADE_NS}invoiceDetails")
    assert len(rows) == 2
    for row in rows:
        cls = row.find(f"{AADE_NS}incomeClassification")
        assert cls.find(f"{CLS_NS}classificationType").text == "E3_561_003"
        assert cls.find(f"{CLS_NS}classificationCategory").text == "category1_1"


def test_one_line_per_rate_and_the_summary_is_their_sum():
    _built, root = _build(_credit(_receipt(), ROWS))
    lines = root.findall(f"{AADE_NS}invoice/{AADE_NS}invoiceDetails")

    assert [line.find(f"{AADE_NS}vatCategory").text for line in lines] == [
        "1",
        "2",
    ]
    assert _text(root, "invoice/invoiceSummary/totalNetValue") == "41.89"
    assert _text(root, "invoice/invoiceSummary/totalVatAmount") == "8.11"
    assert _text(root, "invoice/invoiceSummary/totalGrossValue") == "50.00"
    aggregate = root.findall(
        f"{AADE_NS}invoice/{AADE_NS}invoiceSummary/"
        f"{AADE_NS}incomeClassification"
    )
    assert len(aggregate) == 1
    assert aggregate[0].find(f"{CLS_NS}amount").text == "41.89"


def test_the_payment_row_is_the_credited_amount():
    _built, root = _build(_credit(_receipt(), ROWS))

    assert _text(root, "invoice/paymentMethods/paymentMethodDetails/type") == (
        "7"
    )
    assert (
        _text(root, "invoice/paymentMethods/paymentMethodDetails/amount")
        == "50.00"
    )


def test_a_retry_resends_the_same_identity():
    receipt = _receipt()
    credit = _credit(receipt, ROWS)
    first, _ = _build(credit)
    # What the service persists before the HTTP call.
    credit.mydata_series, credit.mydata_aa = first.series, first.aa

    second, _ = _build(credit)

    assert second.uid == first.uid
    assert (second.series, second.aa) == (first.series, first.aa)
