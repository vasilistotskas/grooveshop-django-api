"""Build the ``InvoicesDoc`` XML payload for a retail document.

Two documents are issued, both over the ERP ``SendInvoices`` channel:

* ``invoiceType=11.1`` (Α.Λ.Π. — retail receipt), from an
  :class:`~order.models.invoice.Invoice`, one line per order item plus
  shipping and fees (``order.taxation``);
* ``invoiceType=11.4`` (retail credit document), from a
  :class:`~order.models.retail_credit.RetailCredit`, one line per VAT rate
  of what is credited. AADE forbids ``correlatedInvoices`` on this type
  (error 205, live-confirmed), so the payload carries no link to the
  receipt; ``specialInvoiceCategory`` 13 ("Δυσχέρεια Συσχέτισης", valid
  for 11.4) is optional in the schema and is not sent.

Invoices (1.1) and credit invoices (5.1) are not built here: AADE decision
Α.1128/2025 art. 1(2) takes them off the ERP channel (see
``order.mydata.types``).

Element ordering inside ``<invoice>`` is fixed by AADE's schema:
``issuer, counterpart, paymentMethods, invoiceHeader, invoiceDetails,
taxesTotals, invoiceSummary`` — see the notes at each element for what the
live API enforces. Wrong order triggers error 101 (XML syntax / schema).
A retail document carries no ``counterpart``.

Decimal handling follows AADE rules: **2 decimal places, ROUND_HALF_UP,
and summary totals MUST equal the sum of the rounded line values**
(errors 203 / 207–210 otherwise). We therefore accumulate the
rounded per-line totals as we emit them, and derive the summary
from those sums — never from the pre-computed bucket aggregates,
which can drift by a cent on multi-item mixed-VAT orders.

Greek text is emitted as native UTF-8 — do NOT HTML-escape (double
encoding triggers schema rejection).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from order.mydata.types import (
    CLASSIFICATION_CATEGORY_GOODS_SALES,
    CLASSIFICATION_TYPE_RETAIL_GOODS,
    INVOICE_TYPE_RETAIL_CREDIT,
    INVOICE_TYPE_RETAIL_RECEIPT,
    PAYMENT_METHOD_CASH,
    PAYMENT_METHOD_POS_CARD,
    PAYMENT_METHOD_WEB_BANKING,
    VAT_CATEGORY_0,
    VAT_CATEGORY_3,
    VAT_CATEGORY_4,
    VAT_CATEGORY_6,
    VAT_CATEGORY_9,
    VAT_CATEGORY_13,
    VAT_CATEGORY_17,
    VAT_CATEGORY_24,
    VAT_EXEMPTION_NO_VAT_ARTICLES,
)
from order.mydata.uid import build_uid
from order.taxation import order_vat_lines
from pay_way.enum.settlement import PaySettlement
from vat.constants import MYDATA_SUPPORTED_VAT_RATES

# VAT rate → AADE ``vatCategory``. Strict whitelist per v1.0.10 annex
# 8.x; unknown rates are treated as a bug upstream (in master data)
# rather than silently remapped to 0% — that would trip error 217
# (missing vatExemptionCategory) downstream, which is a confusing
# way to learn you have bad VAT rows.
_VAT_CATEGORY_BY_RATE: dict[Decimal, int] = {
    Decimal(24): VAT_CATEGORY_24,
    Decimal(17): VAT_CATEGORY_17,
    Decimal(13): VAT_CATEGORY_13,
    Decimal(9): VAT_CATEGORY_9,
    Decimal(6): VAT_CATEGORY_6,
    Decimal(4): VAT_CATEGORY_4,  # Island discount 4% (classic)
    Decimal(3): VAT_CATEGORY_3,  # Law 5057/2023
    Decimal(0): VAT_CATEGORY_0,
}
# ``vat.constants.MYDATA_SUPPORTED_VAT_RATES`` mirrors these keys so the
# ``Vat`` model can reject an unmapped rate at save time (``order``
# cannot be imported from ``vat`` — see that module's docstring for
# why). This assertion is the tripwire that keeps the two from ever
# silently drifting apart: editing one without the other now fails
# at import time instead of at invoice-submission time.
assert set(_VAT_CATEGORY_BY_RATE) == MYDATA_SUPPORTED_VAT_RATES, (
    "_VAT_CATEGORY_BY_RATE and vat.constants.MYDATA_SUPPORTED_VAT_RATES "
    "have drifted apart — update both."
)
# Rates that are NOT taxed — these land in vatCategory=7 and need
# ``vatExemptionCategory`` per AADE error 217.
_ZERO_RATED = Decimal(0)


def _vat_category(rate: Decimal) -> int:
    """Map a numeric VAT rate to the AADE ``vatCategory`` integer.

    Raises ``ValueError`` on unknown rates so bad master data
    surfaces loudly in :func:`order.mydata.service.submit_invoice`
    rather than getting silently rewritten and rejected by AADE
    with a misleading error code."""
    try:
        return _VAT_CATEGORY_BY_RATE[rate]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported VAT rate {rate}% for myDATA — add it to "
            "_VAT_CATEGORY_BY_RATE or fix the Vat row."
        ) from exc


def _money(value: Decimal) -> str:
    """Serialise a ``Decimal`` using AADE's 2dp ``ROUND_HALF_UP``."""
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _emit_detail(
    parent: Element,
    *,
    line_number: int,
    line_net: Decimal,
    line_vat: Decimal,
    rate: Decimal,
    classification_type: str,
    classification_category: str,
) -> None:
    """Append one fully-populated ``<invoiceDetails>`` row.

    Includes the mandatory ``<incomeClassification>`` per AADE error
    314 (every line needs a classification) and the
    ``<vatExemptionCategory>`` disambiguator required when
    ``vatCategory=7`` (error 217)."""
    row = SubElement(parent, "invoiceDetails")
    SubElement(row, "lineNumber").text = str(line_number)
    SubElement(row, "netValue").text = _money(line_net)
    SubElement(row, "vatCategory").text = str(_vat_category(rate))
    SubElement(row, "vatAmount").text = _money(line_vat)
    if rate == _ZERO_RATED:
        SubElement(row, "vatExemptionCategory").text = str(
            VAT_EXEMPTION_NO_VAT_ARTICLES
        )
    _emit_income_classification(
        row,
        amount=line_net,
        classification_type=classification_type,
        classification_category=classification_category,
    )


# AADE hosts the classification type + category + amount in a
# SEPARATE namespace from the invoice body — the "Classificaton"
# spelling (missing `i`) is the official URI per AADE's XSDs, verified
# via live dev response. Do NOT correct the typo.
_INCLS_NS = "https://www.aade.gr/myDATA/incomeClassificaton/v1.0"


def _emit_income_classification(
    parent: Element,
    *,
    amount: Decimal,
    classification_type: str,
    classification_category: str,
) -> None:
    """Append a ``<incomeClassification>`` block. Its inner elements
    live under ``_INCLS_NS`` per AADE's schema — the outer element
    itself stays in the invoice namespace.

    The ``type`` / ``category`` pair is invoiceType-specific; pass
    the correct values from :func:`_classification_pair_for`.
    """
    cls = SubElement(parent, "incomeClassification")
    SubElement(
        cls, f"{{{_INCLS_NS}}}classificationType"
    ).text = classification_type
    SubElement(
        cls, f"{{{_INCLS_NS}}}classificationCategory"
    ).text = classification_category
    SubElement(cls, f"{{{_INCLS_NS}}}amount").text = _money(amount)


@dataclass(frozen=True)
class BuiltInvoice:
    """Result of :func:`build_invoice_xml` — the bytes to transmit
    plus every identity field persisted on the ``Invoice`` row
    BEFORE the HTTP call so retries stay idempotent."""

    xml_bytes: bytes
    uid: str
    invoice_type: str
    series: str
    aa: int


def _pick_payment_type(invoice: Any) -> int:
    """Map the order's payment state to AADE ``paymentMethodDetails.type``.

    ``payment_id`` populated = an online provider (Stripe / Viva /
    etc.) acknowledged the charge → POS / e-POS (code 7). Web Banking
    (6) would only be correct for manual bank-transfer flows; COD
    orders without a provider id → Cash (3).

    ``GIFTCARD_`` payment ids are NOT provider charges — a fully
    gift-card-settled order has no card transaction, so the provider
    branch must not claim POS for it (:func:`_payment_rows` reports
    the gift-card portion under its own configurable code).
    """
    payment_id = getattr(invoice.order, "payment_id", "") or ""
    pay_way = getattr(invoice.order, "pay_way", None)
    if payment_id and not payment_id.startswith("GIFTCARD_"):
        return PAYMENT_METHOD_POS_CARD
    # No transaction ID on file; lean on the pay way's settlement when
    # it exists — an ONLINE pay way without a payment_id is an anomaly
    # (probably mid-flow) so we still pick POS; otherwise cash/COD.
    #
    # Reads ``settlement``, NOT the deprecated ``is_online_payment``
    # mirror it used to, and NOT through ``getattr(..., False)``. That
    # default is what made this dangerous: the mirror columns are
    # scheduled to be dropped, and the moment they are, every
    # payment-id-less order would have been filed to AADE as Cash (3)
    # instead of Web Banking (6) — a wrong tax classification, silently,
    # with no traceback anywhere. ``settlement`` is the authoritative
    # discriminator and an unexpected value raises instead of guessing.
    if pay_way is not None:
        settlement = getattr(pay_way, "settlement", None)
        if settlement and PaySettlement(settlement) == PaySettlement.ONLINE:
            return PAYMENT_METHOD_WEB_BANKING
    return PAYMENT_METHOD_CASH


def _gift_card_payment_type() -> int:
    """AADE payment code for gift-card-settled amounts.

    myDATA has no dedicated voucher code, so the mapping is the
    merchant's accountant's call — configurable via the
    ``MYDATA_GIFT_CARD_PAYMENT_TYPE`` extra setting (default 3 =
    Μετρητά; 5 = Επί πιστώσει is the usual alternative). Invalid
    values fall back to Cash rather than producing a schema error.
    """
    from extra_settings.models import Setting

    try:
        value = int(Setting.get("MYDATA_GIFT_CARD_PAYMENT_TYPE", default=3))
    except TypeError, ValueError:
        value = PAYMENT_METHOD_CASH
    if value not in {1, 2, 3, 4, 5, 6, 7}:
        value = PAYMENT_METHOD_CASH
    return value


def _payment_rows(invoice: Any) -> list[tuple[int, Decimal]]:
    """One ``paymentMethodDetails`` row per settlement source.

    AADE requires the row amounts to SUM exactly to
    ``totalGrossValue``, so a split order (gift card + provider
    remainder) must report two rows — a single POS row for the full
    amount would claim a card capture that never happened. Gift cards
    are payment (multi-purpose voucher), so they settle the invoice
    without reducing its taxable value.
    """
    total = Decimal(invoice.total.amount)
    gift_card = getattr(invoice.order, "gift_card_amount", None)
    gift_amount = (
        min(Decimal(gift_card.amount), total)
        if gift_card and gift_card.amount > 0
        else Decimal(0)
    )
    rows: list[tuple[int, Decimal]] = []
    if gift_amount > 0:
        rows.append((_gift_card_payment_type(), gift_amount))
    remainder = total - gift_amount
    if remainder > 0 or not rows:
        rows.append((_pick_payment_type(invoice), remainder))
    return rows


@dataclass(frozen=True)
class _Line:
    """One ``invoiceDetails`` row: a rate and its net and VAT, to the cent."""

    rate: Decimal
    net: Decimal
    vat: Decimal

    @property
    def gross(self) -> Decimal:
        return self.net + self.vat


def _receipt_lines(invoice: Any) -> list[_Line]:
    """The receipt's lines — the PDF's lines, one rule for both documents
    (``order.taxation``): goods at the rate snapshotted on each item, then
    shipping and the payment-method fee at the rate(s) of the goods they
    travel with. Order-level discounts are already allocated across the
    goods; gift-card amounts are payment and never reduce a taxable
    value."""
    return [
        _Line(rate=line.rate, net=line.net, vat=line.vat)
        for line in order_vat_lines(invoice.order)
    ]


def _credit_lines(credit: Any) -> list[_Line]:
    """The credit document's lines: the VAT breakdown frozen on the row when
    it was issued (``order.credit_documents``), so a retry sends exactly
    what the first attempt did."""
    return [
        _Line(
            rate=Decimal(row["rate"]),
            net=Decimal(row["subtotal"]),
            vat=Decimal(row["vat"]),
        )
        for row in credit.vat_breakdown
    ]


def _credit_payment_rows(credit: Any) -> list[tuple[int, Decimal]]:
    """One row for the whole credited amount, by how the order was paid."""
    return [(_pick_payment_type(credit), Decimal(credit.total.amount))]


def build_receipt_xml(
    invoice: Any,
    *,
    issuer_vat: str,
    issuer_country: str,
    branch: int,
    series_prefix: str,
) -> BuiltInvoice:
    """The ``InvoicesDoc`` for an order's retail receipt (11.1).

    Caller is responsible for passing the resolved seller identity —
    we don't read ``invoice.seller_snapshot`` here so this function
    stays testable without spinning up the full settings stack.

    :param invoice: Persisted ``Invoice`` row.
    :param issuer_vat: Seller ΑΦΜ (no country prefix).
    :param issuer_country: ISO 3166-1 alpha-2 country code (``GR``).
    :param branch: AADE branch number — 0 for main branch.
    :param series_prefix: Configured series prefix; combined with the
        document year to form ``series``.
    """
    return _build(
        invoice,
        invoice_type=INVOICE_TYPE_RETAIL_RECEIPT,
        lines=_receipt_lines(invoice),
        payment_rows=_payment_rows(invoice),
        issuer_vat=issuer_vat,
        issuer_country=issuer_country,
        branch=branch,
        series_prefix=series_prefix,
    )


def build_credit_xml(
    credit: Any,
    *,
    issuer_vat: str,
    issuer_country: str,
    branch: int,
    series_prefix: str,
) -> BuiltInvoice:
    """The ``InvoicesDoc`` for a retail credit document (11.4).

    No ``correlatedInvoices``: AADE answers 205 to one on this type. The
    receipt it credits is known to us only (``RetailCredit.invoice``).
    """
    return _build(
        credit,
        invoice_type=INVOICE_TYPE_RETAIL_CREDIT,
        lines=_credit_lines(credit),
        payment_rows=_credit_payment_rows(credit),
        issuer_vat=issuer_vat,
        issuer_country=issuer_country,
        branch=branch,
        series_prefix=series_prefix,
    )


def _build(
    document: Any,
    *,
    invoice_type: str,
    lines: list[_Line],
    payment_rows: list[tuple[int, Decimal]],
    issuer_vat: str,
    issuer_country: str,
    branch: int,
    series_prefix: str,
) -> BuiltInvoice:
    issue_date: date = document.issue_date
    year = issue_date.year
    # Reuse the persisted ``mydata_series`` on a resubmission (mirrors the
    # ``mydata_aa`` reuse below). Recomputing it from the live
    # ``series_prefix`` would change the ``uid`` if the prefix config was
    # edited between the first submission and a retry, defeating AADE's 228
    # duplicate-uid dedup. NOTE: ``branch`` also feeds the uid and is not
    # persisted, so it must not change between submit and resubmit.
    series = document.mydata_series or f"{series_prefix}-{year}"
    # ``aa`` is the per-year sequential integer its counter owns, baked into
    # the document number; reuse ``mydata_aa`` once pre-allocated.
    aa = document.mydata_aa or _derive_aa_from_document_number(
        document.document_number
    )
    uid = build_uid(
        issuer_vat=issuer_vat,
        issue_date=issue_date,
        branch=branch,
        invoice_type=invoice_type,
        series=series,
        aa=aa,
    )

    # The official AADE namespace is MANDATORY — without it the
    # server can't match any element against the XSD and returns
    # error 101 for every tag.
    root = Element(
        "InvoicesDoc",
        {"xmlns": "http://www.aade.gr/myDATA/invoice/v1.0"},
    )
    invoice_el = SubElement(root, "invoice")

    issuer = SubElement(invoice_el, "issuer")
    SubElement(issuer, "vatNumber").text = issuer_vat
    SubElement(issuer, "country").text = issuer_country
    SubElement(issuer, "branch").text = str(branch)

    # Actual XSD sequence (verified via live AADE dev response): the
    # PDF field-list table orders things differently to the XSD, so
    # don't be fooled by docs — ``invoiceHeader`` must come BEFORE
    # ``paymentMethods`` (error 101 otherwise).
    header = SubElement(invoice_el, "invoiceHeader")
    SubElement(header, "series").text = series
    SubElement(header, "aa").text = str(aa)
    SubElement(header, "issueDate").text = issue_date.isoformat()
    SubElement(header, "invoiceType").text = invoice_type
    SubElement(header, "currency").text = document.currency or "EUR"

    # One row per settlement source; amounts MUST sum to totalGrossValue.
    pm_container = SubElement(invoice_el, "paymentMethods")
    for payment_type, payment_amount in payment_rows:
        pm_row = SubElement(pm_container, "paymentMethodDetails")
        SubElement(pm_row, "type").text = str(payment_type)
        SubElement(pm_row, "amount").text = _money(payment_amount)

    # Accumulate the rounded per-line totals so the summary numbers we emit
    # exactly match the sum of the lines the server sees (errors 203 /
    # 207-210).
    summed_net = Decimal(0)
    summed_vat = Decimal(0)
    summed_gross = Decimal(0)
    classification_totals: dict[tuple[str, str], Decimal] = defaultdict(
        lambda: Decimal(0)
    )
    cls_type = CLASSIFICATION_TYPE_RETAIL_GOODS
    cls_category = CLASSIFICATION_CATEGORY_GOODS_SALES

    for line_number, line in enumerate(lines, start=1):
        _emit_detail(
            invoice_el,
            line_number=line_number,
            line_net=line.net,
            line_vat=line.vat,
            rate=line.rate,
            classification_type=cls_type,
            classification_category=cls_category,
        )
        summed_net += line.net
        summed_vat += line.vat
        summed_gross += line.gross
        classification_totals[(cls_type, cls_category)] += line.net

    # (taxesTotals is omitted: only non-VAT document-level taxes populate
    # it. Leaving it out means AADE derives document-level tax totals from
    # the per-line columns.)
    summary = SubElement(invoice_el, "invoiceSummary")
    SubElement(summary, "totalNetValue").text = _money(summed_net)
    SubElement(summary, "totalVatAmount").text = _money(summed_vat)
    SubElement(summary, "totalWithheldAmount").text = "0.00"
    SubElement(summary, "totalFeesAmount").text = "0.00"
    SubElement(summary, "totalStampDutyAmount").text = "0.00"
    SubElement(summary, "totalOtherTaxesAmount").text = "0.00"
    SubElement(summary, "totalDeductionsAmount").text = "0.00"
    SubElement(summary, "totalGrossValue").text = _money(summed_gross)
    # The aggregate classification block mirrors the sums of the per-line
    # entries by (classificationType, classificationCategory).
    for (type_code, category_code), amount in classification_totals.items():
        _emit_income_classification(
            summary,
            amount=amount,
            classification_type=type_code,
            classification_category=category_code,
        )

    xml_bytes = tostring(root, encoding="utf-8", xml_declaration=True)
    return BuiltInvoice(
        xml_bytes=xml_bytes,
        uid=uid,
        invoice_type=invoice_type,
        series=series,
        aa=aa,
    )


def _derive_aa_from_document_number(number: str) -> int:
    """Extract the integer ``aa`` from ``INV-YYYY-NNNNNN`` (or the credit
    ``CRD-YYYY-NNNNNN``).

    The counter stores it as a plain int; the document number is the
    display form. Raises ``ValueError`` on malformed input: a bug, not a
    recoverable situation.
    """
    tail = number.rsplit("-", 1)[-1]
    return int(tail)
