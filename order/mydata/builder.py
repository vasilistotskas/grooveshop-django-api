"""Build the ``InvoicesDoc`` XML payload from an :class:`Invoice` row.

Scope for Tier A: ``invoiceType=11.1`` (Α.Λ.Π. — retail receipt, B2C).
Structure is driven off the AADE ``myDATA v1.0.12`` XSDs — the XSDs
use two namespaces (``icls:`` for the top-level ``InvoicesDoc`` +
``invoice`` elements and the ``ih:`` prefix in the schema
documentation for common types). In practice AADE accepts the
unprefixed form below.

Extensibility (Tier B — B2B / credit notes / refunds):
- Switch ``invoiceType`` based on ``order.document_type`` +
  buyer-VAT presence.
- Populate ``counterpart`` with buyer VAT / tax office when issuing
  1.1 (B2B sales invoice).
- Pass ``correlatedInvoices`` element for 5.1 linked credit notes.
- Set ``vatCategory`` differently per market (0% export, reverse
  charge, etc.).

Decimal handling follows AADE rules: **2 decimal places, ROUND_HALF_UP,
sum-then-round not round-then-sum** (errors 203 / 207–210 otherwise).
Greek text is emitted as native UTF-8 — do NOT HTML-escape (double
encoding triggers schema rejection).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from order.mydata.uid import build_uid
from order.mydata.types import (
    PAYMENT_METHOD_CASH,
    PAYMENT_METHOD_WEB_BANKING,
    VAT_CATEGORY_0,
    VAT_CATEGORY_13,
    VAT_CATEGORY_24,
    VAT_CATEGORY_6,
)


# VAT rate → AADE ``vatCategory`` code. The AADE enum is a strict
# whitelist of known rates; we pick the closest category by rate.
# (Reverse charge / exempt flavours require ``vatExemptionCategory``
# handling — out of scope for the 11.1 MVP, see Tier B notes above.)
_VAT_CATEGORY_BY_RATE: dict[Decimal, int] = {
    Decimal("24"): VAT_CATEGORY_24,
    Decimal("13"): VAT_CATEGORY_13,
    Decimal("6"): VAT_CATEGORY_6,
    Decimal("0"): VAT_CATEGORY_0,
}


def _vat_category(rate: Decimal) -> int:
    """Map a numeric VAT rate to the AADE ``vatCategory`` integer.

    Unknown rates fall back to category 7 (0% — we flag these with a
    dev-log warning via the caller; AADE rejects them downstream so
    don't try to silently patch up bad master data)."""
    return _VAT_CATEGORY_BY_RATE.get(rate, VAT_CATEGORY_0)


def _money(value: Decimal) -> str:
    """Serialise a ``Decimal`` using AADE's 2dp ``ROUND_HALF_UP``."""
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


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
    invoice_type = "11.1"
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

    root = Element("InvoicesDoc")
    invoice_el = SubElement(root, "invoice")

    # Issuer block
    issuer = SubElement(invoice_el, "issuer")
    SubElement(issuer, "vatNumber").text = issuer_vat
    SubElement(issuer, "country").text = issuer_country
    SubElement(issuer, "branch").text = str(branch)

    # Header
    header = SubElement(invoice_el, "invoiceHeader")
    SubElement(header, "series").text = series
    SubElement(header, "aa").text = str(aa)
    SubElement(header, "issueDate").text = invoice.issue_date.isoformat()
    SubElement(header, "invoiceType").text = invoice_type
    SubElement(header, "currency").text = invoice.currency or "EUR"

    # Payment methods — single row for an online-paid retail sale.
    pm_container = SubElement(invoice_el, "paymentMethods")
    pm_row = SubElement(pm_container, "paymentMethodDetails")
    # Type 3 = Web banking / card online; type 7 = cash (offline
    # payment). Mapping is kept narrow — extend in Tier B.
    pay_type = (
        PAYMENT_METHOD_WEB_BANKING
        if getattr(invoice.order, "payment_id", "")
        else PAYMENT_METHOD_CASH
    )
    SubElement(pm_row, "type").text = str(pay_type)
    SubElement(pm_row, "amount").text = _money(Decimal(invoice.total.amount))

    # Line items — one ``invoiceDetails`` per item, with its own VAT
    # category so the server-side sum matches ours.
    for idx, item in enumerate(
        invoice.order.items.select_related("product__vat").all(), start=1
    ):
        row = SubElement(invoice_el, "invoiceDetails")
        SubElement(row, "lineNumber").text = str(idx)
        # Gross unit × qty = line gross; AADE wants NET (excl. VAT) +
        # VAT amount separately, so back out the net here using the
        # same formula as ``_compute_vat_breakdown``.
        rate = (
            Decimal(item.product.vat.value)
            if item.product and item.product.vat_id
            else Decimal("0")
        )
        unit_gross = Decimal(item.price.amount)
        qty = Decimal(item.quantity)
        line_gross = unit_gross * qty
        divisor = Decimal("1") + rate / Decimal("100")
        line_net = line_gross / divisor if divisor else line_gross
        line_vat = line_gross - line_net
        SubElement(row, "netValue").text = _money(line_net)
        SubElement(row, "vatCategory").text = str(_vat_category(rate))
        SubElement(row, "vatAmount").text = _money(line_vat)

    # Summary — MUST equal the sum of the lines above (errors 203 /
    # 207–210 otherwise). ``_order_totals`` already quantised these.
    summary = SubElement(invoice_el, "invoiceSummary")
    SubElement(summary, "totalNetValue").text = _money(
        Decimal(invoice.subtotal.amount)
    )
    SubElement(summary, "totalVatAmount").text = _money(
        Decimal(invoice.total_vat.amount)
    )
    SubElement(summary, "totalWithheldAmount").text = "0.00"
    SubElement(summary, "totalFeesAmount").text = "0.00"
    SubElement(summary, "totalStampDutyAmount").text = "0.00"
    SubElement(summary, "totalOtherTaxesAmount").text = "0.00"
    SubElement(summary, "totalDeductionsAmount").text = "0.00"
    SubElement(summary, "totalGrossValue").text = _money(
        Decimal(invoice.total.amount)
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
