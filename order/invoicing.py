"""Invoice generation service.

Given an :class:`Order`, this module renders a Greek-tax-law compliant
PDF invoice, allocates an atomic sequential number via
:class:`InvoiceCounter`, and stores the result in private media so it
can only be downloaded via a signed URL from the order's owner.

The heavy lifting is split into small helpers so the unit tests can
pin ``_compute_vat_breakdown`` and ``_build_context`` without spinning
up WeasyPrint's native stack:

- :func:`generate_invoice` — idempotent entry point; returns the
  existing Invoice if one already exists for the order.
- :func:`_compute_vat_breakdown` — aggregates order items by VAT rate
  so the invoice shows one row per rate band (24 %, 13 %, 6 %, 0 %).
- :func:`_build_context` — builds the template context dict.
- :func:`_render_pdf_bytes` — calls WeasyPrint; imported lazily so
  machines without cairo/pango (Windows dev) can still import this
  module for tests that mock the render step.

Seller metadata (company name, VAT ID / AFM, registration number,
address) is read from :class:`extra_settings.models.Setting` so ops
can change it without a migration. Missing settings fall back to
reasonable placeholders that make it obvious in dev — the field names
are documented in ``INVOICE_SELLER_SETTING_KEYS`` below.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import override as translation_override

from extra_settings.models import Setting

from core.utils.i18n import get_order_language
from order.models.invoice import Invoice, InvoiceCounter
from order.models.order import Order

logger = logging.getLogger(__name__)


# Setting keys for seller info — documented so ops / tests can find
# them without grepping template context. Defaults are intentionally
# obvious-in-dev ("Grooveshop" / empty VAT ID) so an unconfigured
# environment produces a recognisable invoice rather than silently
# shipping a plausible-but-wrong one.
INVOICE_SELLER_SETTING_KEYS = {
    "name": "INVOICE_SELLER_NAME",
    "vat_id": "INVOICE_SELLER_VAT_ID",
    "registration_number": "INVOICE_SELLER_REGISTRATION_NUMBER",
    "address_line_1": "INVOICE_SELLER_ADDRESS_LINE_1",
    "address_line_2": "INVOICE_SELLER_ADDRESS_LINE_2",
    "city": "INVOICE_SELLER_CITY",
    "postal_code": "INVOICE_SELLER_POSTAL_CODE",
    "country": "INVOICE_SELLER_COUNTRY",
    "phone": "INVOICE_SELLER_PHONE",
    "email": "INVOICE_SELLER_EMAIL",
}


class InvoiceAlreadyExists(Exception):
    """Raised when :func:`generate_invoice` is called with ``force=False``
    against an order that already has an :class:`Invoice` row."""


def _seller_snapshot() -> dict[str, str]:
    """Resolve seller info from ``extra_settings`` with dev-friendly defaults.

    Falls back to ``settings.SITE_NAME`` / ``INFO_EMAIL`` when the
    dedicated invoice settings haven't been populated — lets a fresh
    install render SOMETHING without failing at render time.
    """
    default_name = getattr(settings, "SITE_NAME", "Grooveshop")
    default_email = getattr(settings, "INFO_EMAIL", "")
    resolved = {}
    for field, key in INVOICE_SELLER_SETTING_KEYS.items():
        default = ""
        if field == "name":
            default = default_name
        elif field == "email":
            default = default_email
        resolved[field] = Setting.get(key, default=default)
    return resolved


def _buyer_snapshot(order: Order) -> dict[str, str]:
    """Capture the buyer fields at issue time (order state is mutable
    until the invoice is frozen)."""
    return {
        "name": f"{order.first_name} {order.last_name}".strip(),
        "email": order.email,
        "phone": str(order.phone) if order.phone else "",
        "address_line_1": f"{order.street} {order.street_number}".strip(),
        "address_line_2": order.place or "",
        "city": order.city,
        "postal_code": order.zipcode,
        "country": (order.country.name if order.country_id else "")
        if hasattr(order, "country")
        else "",
        "region": (order.region.name if order.region_id else "")
        if hasattr(order, "region")
        else "",
    }


def _compute_vat_breakdown(order: Order) -> list[dict[str, Any]]:
    """Aggregate order items by VAT rate.

    Returns one dict per rate band sorted by rate descending:
    ``{rate, subtotal, vat, gross}`` where values are decimals in the
    order's currency. Items without a configured VAT are grouped under
    rate=0 so the invoice still balances.
    """
    buckets: dict[Decimal, dict[str, Decimal]] = defaultdict(
        lambda: {
            "subtotal": Decimal("0"),
            "vat": Decimal("0"),
            "gross": Decimal("0"),
        }
    )

    for item in order.items.select_related("product__vat").all():
        unit_gross = Decimal(item.price.amount)
        quantity = Decimal(item.quantity)
        line_gross = unit_gross * quantity

        rate = Decimal("0")
        if item.product and item.product.vat_id:
            rate = Decimal(item.product.vat.value)

        # Item prices are VAT-inclusive (final prices), so back out the
        # VAT component: subtotal = gross / (1 + rate/100).
        divisor = Decimal("1") + rate / Decimal("100")
        line_subtotal = (line_gross / divisor) if divisor else line_gross
        line_vat = line_gross - line_subtotal

        bucket = buckets[rate]
        bucket["subtotal"] += line_subtotal
        bucket["vat"] += line_vat
        bucket["gross"] += line_gross

    # Stable ordering — highest rate first is the Greek convention.
    return [
        {
            "rate": str(rate),
            "subtotal": str(values["subtotal"].quantize(Decimal("0.01"))),
            "vat": str(values["vat"].quantize(Decimal("0.01"))),
            "gross": str(values["gross"].quantize(Decimal("0.01"))),
        }
        for rate, values in sorted(buckets.items(), reverse=True)
    ]


def _order_totals(
    order: Order, vat_breakdown: list[dict[str, Any]]
) -> dict[str, Decimal]:
    """Derive invoice totals from the VAT breakdown so rounding adds up."""
    subtotal = sum(
        Decimal(row["subtotal"]) for row in vat_breakdown
    ) or Decimal("0")
    total_vat = sum(Decimal(row["vat"]) for row in vat_breakdown) or Decimal(
        "0"
    )
    gross = subtotal + total_vat
    shipping = (
        Decimal(order.shipping_price.amount)
        if order.shipping_price
        else Decimal("0")
    )
    payment_fee = (
        Decimal(order.payment_method_fee.amount)
        if order.payment_method_fee
        else Decimal("0")
    )
    total = gross + shipping + payment_fee
    return {
        "subtotal": subtotal,
        "total_vat": total_vat,
        "shipping": shipping,
        "payment_fee": payment_fee,
        "total": total,
    }


def _build_context(
    order: Order,
    invoice: Invoice,
    vat_breakdown: list[dict[str, Any]],
    totals: dict[str, Decimal],
) -> dict[str, Any]:
    """Assemble the template context dict.

    Kept separate from the render call so tests can assert exact
    content without going through WeasyPrint.
    """
    return {
        "invoice": invoice,
        "order": order,
        "items": list(order.items.select_related("product__vat").all()),
        "seller": invoice.seller_snapshot,
        "buyer": invoice.buyer_snapshot,
        "vat_breakdown": vat_breakdown,
        "totals": totals,
        "currency": invoice.currency,
    }


def _render_pdf_bytes(context: dict[str, Any]) -> bytes:
    """Render ``invoices/invoice.html`` to PDF bytes via WeasyPrint.

    Imported lazily so importing this module doesn't hard-require the
    native GTK stack (WeasyPrint's dependency) — tests that mock the
    render step can therefore run on machines without cairo/pango.
    """
    from weasyprint import HTML

    html_string = render_to_string("invoices/invoice.html", context)
    return HTML(string=html_string).write_pdf()


@transaction.atomic
def generate_invoice(order: Order, *, force: bool = False) -> Invoice:
    """Idempotent invoice generation for a single order.

    Returns the existing Invoice if one exists unless ``force=True``.
    Never fabricates sequential numbers — always routes through
    :meth:`InvoiceCounter.allocate`.

    Raises :class:`InvoiceAlreadyExists` only when ``force=False`` and
    an invoice is already attached and the caller opted into strict
    mode (see ``tasks.generate_order_invoice`` for how the retryable
    Celery task uses this).
    """
    existing = Invoice.objects.filter(order_id=order.id).first()
    if existing and not force:
        logger.debug(
            "Invoice %s already exists for order %s — returning existing",
            existing.invoice_number,
            order.id,
        )
        return existing

    issue_date = timezone.localdate()
    invoice_number = InvoiceCounter.allocate(issue_date.year)

    vat_breakdown = _compute_vat_breakdown(order)
    totals = _order_totals(order, vat_breakdown)
    currency = (
        order.paid_amount.currency.code
        if order.paid_amount
        else settings.DEFAULT_CURRENCY
    )

    invoice = existing if existing and force else Invoice(order=order)
    invoice.invoice_number = invoice_number
    invoice.issue_date = issue_date
    invoice.seller_snapshot = _seller_snapshot()
    invoice.buyer_snapshot = _buyer_snapshot(order)
    invoice.vat_breakdown = vat_breakdown
    invoice.subtotal = totals["subtotal"]
    invoice.total_vat = totals["total_vat"]
    invoice.total = totals["total"]
    invoice.currency = currency
    # Save first so ``invoice.pk`` exists when the upload_to callable
    # builds the file path.
    invoice.save()

    # Render under the buyer's preferred language so e.g. a German
    # shopper gets a German-labelled invoice even though the seller
    # operates primarily in Greek.
    language = get_order_language(order)
    with translation_override(language):
        pdf_bytes = _render_pdf_bytes(
            _build_context(order, invoice, vat_breakdown, totals)
        )

    filename = f"{invoice.invoice_number}.pdf"
    invoice.document_file.save(filename, ContentFile(pdf_bytes), save=False)
    invoice.save(update_fields=["document_file"])

    logger.info(
        "Generated invoice %s for order %s (%s bytes)",
        invoice.invoice_number,
        order.id,
        len(pdf_bytes),
    )
    return invoice
