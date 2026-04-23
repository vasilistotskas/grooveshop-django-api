"""Build the ``InvoicesDoc`` XML payload from an :class:`Invoice` row.

Scope for Tier A: ``invoiceType=11.1`` (Α.Λ.Π. — retail receipt, B2C).
Structure is driven off the AADE ``myDATA v1.0.10`` XSDs and validated
against the official error catalogue (PDF bundled under ``docs/``).

Element ordering inside ``<invoice>`` is fixed by AADE's schema
(PDF line 667): ``issuer, counterpart, paymentMethods,
invoiceHeader, invoiceDetails, taxesTotals, invoiceSummary``. Wrong
order triggers error 101 (XML syntax / schema).

Extensibility (Tier B — B2B / credit notes / refunds):
- Switch ``invoiceType`` based on ``order.document_type`` +
  buyer-VAT presence.
- Populate ``counterpart`` with buyer VAT / tax office when issuing
  1.1 (B2B sales invoice).
- Pass ``correlatedInvoices`` (inside InvoiceHeader) for 5.1 linked
  credit notes.
- Populate ``vatExemptionCategory`` per export / reverse-charge kind.

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
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from order.mydata.types import (
    CLASSIFICATION_CATEGORY_B2B_MERCHANDISE,
    CLASSIFICATION_CATEGORY_GOODS_SALES,
    CLASSIFICATION_TYPE_B2B_DOMESTIC,
    CLASSIFICATION_TYPE_RETAIL_GOODS,
    INVOICE_TYPE_B2B_SALES,
    INVOICE_TYPE_B2C_RETAIL,
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


# VAT rate → AADE ``vatCategory``. Strict whitelist per v1.0.10 annex
# 8.x; unknown rates are treated as a bug upstream (in master data)
# rather than silently remapped to 0% — that would trip error 217
# (missing vatExemptionCategory) downstream, which is a confusing
# way to learn you have bad VAT rows.
_VAT_CATEGORY_BY_RATE: dict[Decimal, int] = {
    Decimal("24"): VAT_CATEGORY_24,
    Decimal("17"): VAT_CATEGORY_17,
    Decimal("13"): VAT_CATEGORY_13,
    Decimal("9"): VAT_CATEGORY_9,
    Decimal("6"): VAT_CATEGORY_6,
    Decimal("4"): VAT_CATEGORY_4,  # Island discount 4% (classic)
    Decimal("3"): VAT_CATEGORY_3,  # Law 5057/2023
    Decimal("0"): VAT_CATEGORY_0,
}
# Rates that are NOT taxed — these land in vatCategory=7 and need
# ``vatExemptionCategory`` per AADE error 217.
_ZERO_RATED = Decimal("0")


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


def _split_gross(line_gross: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """Back out net + VAT from a gross amount, each quantised to 2dp.

    Returning the ROUNDED values is essential — the per-line and
    summary columns must agree after AADE's server-side re-rounding
    (errors 203 / 207–210 otherwise)."""
    divisor = Decimal("1") + rate / Decimal("100")
    line_net = line_gross / divisor if divisor else line_gross
    line_vat = line_gross - line_net
    return (
        Decimal(_money(line_net)),
        Decimal(_money(line_vat)),
    )


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
    314 (every 11.1/1.1 line needs a classification) and the
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


def _ancillary_charges(order: Any):
    """Yield the customer-paid amounts that sit outside ``OrderItem``
    rows (shipping, payment-method fee). Empty / zero values are
    skipped — AADE rejects zero-amount lines."""
    shipping = getattr(order, "shipping_price", None)
    if shipping is not None and shipping.amount > 0:
        yield Decimal(shipping.amount)
    fee = getattr(order, "payment_method_fee", None)
    if fee is not None and fee.amount > 0:
        yield Decimal(fee.amount)


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


def _normalise_buyer_vat(raw: str) -> str:
    """Strip optional ``EL`` / ``GR`` prefix from a Greek ΑΦΜ.

    AADE rejects prefixed VATs with error 104 ("Invalid Greek VAT
    number"), but users routinely enter the VIES form. Normalising
    once here keeps the rest of the pipeline assumption-free."""
    cleaned = (raw or "").strip().upper()
    if cleaned.startswith(("EL", "GR")):
        cleaned = cleaned[2:]
    return cleaned.strip()


def _pick_invoice_type(order: Any, issuer_country: str) -> str:
    """Determine the AADE ``invoiceType`` from Order state.

    Tier B supports only 11.1 (B2C retail) and 1.1 (domestic B2B).
    Intra-EU (1.2) and third-country (1.3) are deferred to Tier C
    because they additionally need reverse-charge VAT exemption
    handling and classification swaps (``E3_561_005`` / ``006``).
    """
    buyer_vat = _normalise_buyer_vat(getattr(order, "billing_vat_id", "") or "")
    if not buyer_vat:
        return INVOICE_TYPE_B2C_RETAIL
    buyer_country = (
        (getattr(order, "billing_country", "") or "").strip().upper()
    )
    # An empty buyer_country defaults to the issuer's country — most
    # GR e-commerce B2B orders leave it blank and are domestic by
    # default.
    effective_buyer_country = buyer_country or issuer_country.upper()
    if effective_buyer_country == issuer_country.upper():
        return INVOICE_TYPE_B2B_SALES
    # Non-domestic B2B: out of scope for Tier B. Fall back to 11.1
    # (receipt) rather than guessing 1.2/1.3 wrong. The service layer
    # blocks ``document_type=INVOICE + foreign VAT`` upstream so this
    # branch is defence in depth only.
    return INVOICE_TYPE_B2C_RETAIL


def _classification_pair_for(invoice_type: str) -> tuple[str, str]:
    """Return the ``(classificationType, classificationCategory)``
    that AADE requires for the given ``invoiceType``."""
    if invoice_type == INVOICE_TYPE_B2B_SALES:
        return (
            CLASSIFICATION_TYPE_B2B_DOMESTIC,
            CLASSIFICATION_CATEGORY_B2B_MERCHANDISE,
        )
    # Default: 11.1 retail.
    return (
        CLASSIFICATION_TYPE_RETAIL_GOODS,
        CLASSIFICATION_CATEGORY_GOODS_SALES,
    )


def _emit_counterpart(
    parent: Element,
    *,
    vat_number: str,
    country: str,
    issuer_country: str,
    branch: int = 0,
) -> None:
    """Append a ``<counterpart>`` block for B2B invoices.

    Per AADE v1.0.10 §5.1 + error 220: ``<name>`` is FORBIDDEN when
    the counterpart country equals the issuer's (Greek party
    emitting to a Greek party). Only vatNumber/country/branch are
    sent for domestic B2B. Tier C will extend this for non-GR
    counterparts that need ``<name>`` and ``<address>``."""
    counterpart = SubElement(parent, "counterpart")
    SubElement(counterpart, "vatNumber").text = vat_number
    SubElement(counterpart, "country").text = country.upper()
    SubElement(counterpart, "branch").text = str(branch)


def _pick_payment_type(invoice: Any) -> int:
    """Map the order's payment state to AADE ``paymentMethodDetails.type``.

    ``payment_id`` populated = an online provider (Stripe / Viva /
    etc.) acknowledged the charge → POS / e-POS (code 7). Web Banking
    (6) would only be correct for manual bank-transfer flows; COD
    orders without a provider id → Cash (3).
    """
    payment_id = getattr(invoice.order, "payment_id", "") or ""
    pay_way = getattr(invoice.order, "pay_way", None)
    if payment_id:
        return PAYMENT_METHOD_POS_CARD
    # No transaction ID on file; lean on the PayWay flag when it
    # exists — an "online" pay way without a payment_id is an
    # anomaly (probably mid-flow) so we still pick POS; otherwise
    # we treat as cash/COD.
    if pay_way is not None and getattr(pay_way, "is_online_payment", False):
        return PAYMENT_METHOD_WEB_BANKING
    return PAYMENT_METHOD_CASH


def build_invoice_xml(
    invoice: Any,
    *,
    issuer_vat: str,
    issuer_country: str,
    branch: int,
    series_prefix: str,
) -> BuiltInvoice:
    """Return the ``InvoicesDoc`` XML for a single :class:`Invoice`.

    Caller is responsible for passing the resolved seller identity —
    we don't read ``invoice.seller_snapshot`` here so this function
    stays testable without spinning up the full settings stack.

    :param invoice: Persisted ``Invoice`` row with ``vat_breakdown`` /
        ``totals`` already populated.
    :param issuer_vat: Seller ΑΦΜ (no country prefix).
    :param issuer_country: ISO 3166-1 alpha-2 country code (``GR``).
    :param branch: AADE branch number — 0 for main branch.
    :param series_prefix: Configured series prefix; combined with the
        invoice year to form ``series``.
    """
    order = invoice.order
    invoice_type = _pick_invoice_type(order, issuer_country=issuer_country)
    year = invoice.issue_date.year
    series = f"{series_prefix}-{year}"
    # ``aa`` is the per-year sequential integer; ``InvoiceCounter``
    # already owns it, so reuse ``mydata_aa`` if pre-allocated (on
    # resubmission) else derive from the counter number baked into
    # ``invoice_number``.
    aa = invoice.mydata_aa or _derive_aa_from_invoice_number(
        invoice.invoice_number
    )
    uid = build_uid(
        issuer_vat=issuer_vat,
        issue_date=invoice.issue_date,
        branch=branch,
        invoice_type=invoice_type,
        series=series,
        aa=aa,
    )

    # The official AADE namespace is MANDATORY — without it the
    # server can't match any element against the XSD and returns
    # error 101 for every tag ("Could not find schema information
    # for the element 'X'"). Kept as a literal string rather than
    # a module-level constant so the schema version bump (v1.0.10 →
    # v1.0.12) is a single textual change.
    root = Element(
        "InvoicesDoc",
        {"xmlns": "http://www.aade.gr/myDATA/invoice/v1.0"},
    )
    invoice_el = SubElement(root, "invoice")

    # ── issuer ──────────────────────────────────────────────────
    issuer = SubElement(invoice_el, "issuer")
    SubElement(issuer, "vatNumber").text = issuer_vat
    SubElement(issuer, "country").text = issuer_country
    SubElement(issuer, "branch").text = str(branch)

    # ── counterpart (B2B only) ──────────────────────────────────
    # 11.1 retail must NOT emit counterpart (AADE error 220 for
    # Greek counterpart name, plus "forbidden for invoice type"
    # errors on other counterpart fields). 1.1 B2B REQUIRES it
    # — without vatNumber + country the invoice is malformed.
    if invoice_type == INVOICE_TYPE_B2B_SALES:
        buyer_vat = _normalise_buyer_vat(
            getattr(order, "billing_vat_id", "") or ""
        )
        buyer_country = (
            getattr(order, "billing_country", "") or ""
        ).strip().upper() or issuer_country.upper()
        _emit_counterpart(
            invoice_el,
            vat_number=buyer_vat,
            country=buyer_country,
            issuer_country=issuer_country,
        )

    # ── invoiceHeader ───────────────────────────────────────────
    # Actual XSD sequence (verified via live AADE dev response): the
    # PDF field-list table orders things differently to the XSD, so
    # don't be fooled by docs — ``invoiceHeader`` must come BEFORE
    # ``paymentMethods`` (error 101 otherwise, with a helpful
    # "expected invoiceHeader" message).
    header = SubElement(invoice_el, "invoiceHeader")
    SubElement(header, "series").text = series
    SubElement(header, "aa").text = str(aa)
    SubElement(header, "issueDate").text = invoice.issue_date.isoformat()
    SubElement(header, "invoiceType").text = invoice_type
    SubElement(header, "currency").text = invoice.currency or "EUR"

    # ── paymentMethods ──────────────────────────────────────────
    pm_container = SubElement(invoice_el, "paymentMethods")
    pm_row = SubElement(pm_container, "paymentMethodDetails")
    SubElement(pm_row, "type").text = str(_pick_payment_type(invoice))
    SubElement(pm_row, "amount").text = _money(Decimal(invoice.total.amount))

    # ── invoiceDetails ──────────────────────────────────────────
    # Accumulate rounded per-line totals + per-classification sums so
    # the summary numbers we emit exactly match the sum of the lines
    # the server sees (avoids errors 203 / 207–210). Shipping and
    # payment-method fees are emitted as extra lines so
    # ``paymentMethods.amount`` still equals the invoice gross.
    summed_net = Decimal("0")
    summed_vat = Decimal("0")
    summed_gross = Decimal("0")
    classification_totals: dict[tuple[str, str], Decimal] = defaultdict(
        lambda: Decimal("0")
    )
    cls_type, cls_category = _classification_pair_for(invoice_type)

    line_number = 0
    for item in invoice.order.items.select_related("product__vat").all():
        rate = (
            Decimal(item.product.vat.value)
            if item.product and item.product.vat_id
            else Decimal("0")
        )
        line_gross = Decimal(item.price.amount) * Decimal(item.quantity)
        line_net, line_vat = _split_gross(line_gross, rate)
        line_number += 1
        _emit_detail(
            invoice_el,
            line_number=line_number,
            line_net=line_net,
            line_vat=line_vat,
            rate=rate,
            classification_type=cls_type,
            classification_category=cls_category,
        )
        summed_net += line_net
        summed_vat += line_vat
        summed_gross += line_net + line_vat
        classification_totals[(cls_type, cls_category)] += line_net

    # Shipping + payment-method fee — customer-paid amounts that sit
    # outside ``OrderItem`` rows. Treated as VAT 24% (standard GR
    # domestic rate). Tier C will thread through per-order overrides
    # for exports / island rates.
    for gross_decimal in _ancillary_charges(invoice.order):
        line_net, line_vat = _split_gross(gross_decimal, Decimal("24"))
        line_number += 1
        _emit_detail(
            invoice_el,
            line_number=line_number,
            line_net=line_net,
            line_vat=line_vat,
            rate=Decimal("24"),
            classification_type=cls_type,
            classification_category=cls_category,
        )
        summed_net += line_net
        summed_vat += line_vat
        summed_gross += line_net + line_vat
        classification_totals[(cls_type, cls_category)] += line_net

    # (taxesTotals is omitted for 11.1 — only non-VAT document-level
    # taxes populate it. Leaving it out means AADE derives document-
    # level tax totals from the per-line columns.)

    # ── invoiceSummary ──────────────────────────────────────────
    summary = SubElement(invoice_el, "invoiceSummary")
    SubElement(summary, "totalNetValue").text = _money(summed_net)
    SubElement(summary, "totalVatAmount").text = _money(summed_vat)
    SubElement(summary, "totalWithheldAmount").text = "0.00"
    SubElement(summary, "totalFeesAmount").text = "0.00"
    SubElement(summary, "totalStampDutyAmount").text = "0.00"
    SubElement(summary, "totalOtherTaxesAmount").text = "0.00"
    SubElement(summary, "totalDeductionsAmount").text = "0.00"
    SubElement(summary, "totalGrossValue").text = _money(summed_gross)
    # Aggregate classification block MUST mirror the sums of the
    # per-line entries by (classificationType, classificationCategory).
    # Tier B still emits a single combo per invoice (retail OR B2B),
    # so this loop runs once; the data structure is ready for Tier C
    # where mixed baskets (e.g. goods + services) will produce
    # multiple rows.
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


def _derive_aa_from_invoice_number(invoice_number: str) -> int:
    """Extract the integer ``aa`` from ``INV-YYYY-NNNNNN``.

    The counter stores it as a plain int; our ``invoice_number`` is
    the display form. Raises ``ValueError`` on malformed input — a
    bug, not a recoverable situation.
    """
    tail = invoice_number.rsplit("-", 1)[-1]
    return int(tail)
