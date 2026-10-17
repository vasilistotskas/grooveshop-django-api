"""Retail receipt generation service.

Given an :class:`Order`, this module renders the PDF of its retail receipt
(the platform's myDATA 11.1 document, issued when the order ships),
allocates an atomic sequential number via :class:`InvoiceCounter`, and
stores the result in private media. The customer-facing download goes
through ``OrderViewSet.invoice_download`` which streams the bytes via
Django auth — no raw storage URLs are ever exposed to clients. The PDF of
a retail credit document (11.4) is rendered here too
(:func:`render_credit_pdf`).

The platform issues no invoice (Τιμολόγιο): from 2.11.2026 AADE decision
Α.1128/2025 requires invoices to come from a licensed provider or AADE's
timologio app, so ``document_type=INVOICE`` orders never reach this
module (see ``order.mydata.types``).

The heavy lifting is split into small helpers so the unit tests can
pin ``_compute_vat_breakdown`` and ``_build_context`` without spinning
up WeasyPrint's native stack:

- :func:`generate_invoice` — idempotent entry point; returns the
  existing Invoice if one already exists for the order, and refuses
  (:class:`ReceiptNotIssuable`) an order the platform issues no receipt
  for.
- :func:`_compute_vat_breakdown` — aggregates the order's taxable lines
  (goods, shipping, payment fee; ``order.taxation``) by VAT rate so the
  invoice shows one row per rate band (24 %, 13 %, 6 %, 0 %).
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
from decimal import Decimal
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import override as translation_override
from extra_settings.models import Setting

from core.utils.i18n import get_order_language
from core.utils.tenant_urls import get_tenant_frontend_url
from order.discounts import order_discount_total
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus
from order.models.invoice import Invoice, InvoiceCounter
from order.models.order import Order
from order.models.retail_credit import RetailCredit
from order.mydata.config import load_config as load_mydata_config
from order.taxation import breakdown_by_rate, order_vat_lines
from pay_way.enum.pay_way import PayWayEnum
from tenant.credentials import tenant_contact_email, tenant_site_name
from tenant.membership import is_demo_store

logger = logging.getLogger(__name__)


# The seller's identity is not an invoicing concept — the same facts are
# published on the storefront under e-Commerce Directive art. 5 and
# N. 4919/2022 art. 22. One definition lives in ``tenant.legal_identity``
# so an invoice can never name a different seller than the site does.
from tenant.legal_identity import SELLER_SETTING_KEYS  # noqa: E402

INVOICE_SELLER_SETTING_KEYS = SELLER_SETTING_KEYS


#: Statuses from which a retail receipt may be issued: the goods are on
#: their way or were. ΕΛΠ art. 13(a) dates the receipt to "the delivery or
#: the start of the shipment", so SHIPPED is where it is issued; the later
#: ones are for an order whose receipt was missed.
RECEIPT_ISSUING_STATUSES = frozenset(
    {
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.COMPLETED,
        OrderStatus.RETURNED,
    }
)


def receipt_issuable(order: Order) -> bool:
    """True when the platform issues this order's retail receipt now: a
    receipt order (an invoice order never gets one) whose goods have
    shipped."""
    return (
        order.document_type == OrderDocumentTypeEnum.RECEIPT.value
        and order.status in RECEIPT_ISSUING_STATUSES
    )


def specimen_available(order: Order) -> bool:
    """True when the order may show a specimen receipt: the store is a
    demo store (``tenant.membership.is_demo_store``) and the
    order is one a real receipt would be issued for. A specimen is a
    showcase of the document, never a tax document, so no other store
    offers it."""
    return is_demo_store() and receipt_issuable(order)


class SpecimenNotAvailable(Exception):
    """:func:`render_specimen_pdf` refused: the store is not a demo store,
    or the order is not one a receipt is issued for."""


class ReceiptNotIssuable(Exception):
    """:func:`generate_invoice` refused: the platform issues no receipt
    for this order now — it is not a shipped receipt order, or the store
    issues its receipts outside the platform (``MyDataConfig.is_ready``) —
    or a regeneration was asked for an order that has no receipt."""


def _seller_snapshot() -> dict[str, str]:
    """Resolve seller info from ``extra_settings`` with dev-friendly defaults.

    Falls back to ``tenant_site_name()`` / ``INFO_EMAIL`` when the
    dedicated invoice settings haven't been populated — lets a fresh
    install render SOMETHING without failing at render time.
    """
    default_name = tenant_site_name()
    default_email = tenant_contact_email()
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
    until the document is frozen).

    ΕΛΠ art. 12(4) has the seller keep the customer's name and address for
    every credit document above EUR 50: the credit documents render this
    snapshot, taken with the receipt they credit.
    """
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
    """Aggregate the order's taxable lines by VAT rate.

    Returns one dict per rate band sorted by rate descending (the Greek
    convention): ``{rate, subtotal, vat, gross}`` where values are
    decimals in the order's currency. The bands cover goods, shipping and
    the payment-method fee alike — the same lines the myDATA payload is
    built from (``order.taxation``), at the rates snapshotted on the
    order items, so a ``Vat`` row edited or deleted after checkout changes
    nothing here.

    Order-level discounts (promotions + loyalty) are allocated across
    the lines first — VAT is owed on what the customer actually paid.
    Gift-card amounts are payment, never allocated (order/discounts.py).
    """
    return breakdown_by_rate(order_vat_lines(order))


def _order_totals(
    order: Order, vat_breakdown: list[dict[str, Any]]
) -> dict[str, Decimal]:
    """Derive invoice totals from the VAT breakdown so rounding adds up.

    The breakdown already holds the shipping and fee shares at their VAT
    rates, so net + VAT is the whole document and equals myDATA's
    ``invoiceSummary``. ``shipping`` and ``payment_fee`` are the gross
    amounts charged, kept for the PDF's reference rows; like ``discount``
    they never enter the total arithmetic.
    """
    subtotal = sum(
        Decimal(row["subtotal"]) for row in vat_breakdown
    ) or Decimal(0)
    total_vat = sum(Decimal(row["vat"]) for row in vat_breakdown) or Decimal(0)
    return {
        "subtotal": subtotal,
        "total_vat": total_vat,
        "shipping": Decimal(order.shipping_price.amount),
        "payment_fee": Decimal(order.payment_method_fee.amount),
        "discount": order_discount_total(order),
        "total": subtotal + total_vat,
    }


def _pay_way_display(order: Order) -> str:
    """Human-readable payment method label for the invoice.

    Prefers ``PayWay.display_name`` — the resolved label, not the raw
    ``PayWayEnum`` key the column stores. Reading the column directly
    printed "Method: PAY_ON_DELIVERY" on a Greek tax document.

    Falls back to the order's snapshot key (the pay-way row can be
    deleted; the FK is ``SET_NULL``), then to ``Order.payment_method``
    (raw gateway code like ``stripe`` / ``viva_wallet`` / ``acs_cod``,
    written by the payment-confirmation handlers), then empty.
    """
    pay_way = getattr(order, "pay_way", None)
    if pay_way is not None:
        label = pay_way.display_name
        if label:
            return label

    snapshot = getattr(order, "pay_way_key", "") or ""
    if snapshot:
        try:
            return str(PayWayEnum(snapshot).label)
        except ValueError:
            return snapshot

    return order.payment_method or ""


def _verification_url(order: Order, *, language: str) -> str:
    """Public-facing order URL the QR code points at.

    Lands on the customer's order detail page on the storefront —
    scanning redirects a logged-in customer straight to their copy of
    the invoice download button. Guests hit the login wall; once the
    order is claimed via its UUID the Nuxt page re-renders. *language*
    is the one the invoice is rendered in.
    """
    return get_tenant_frontend_url(
        f"/account/orders/{order.id}", language=language
    )


def _build_qr_svg(url: str) -> str:
    """Inline SVG QR code for ``url``.

    Uses ``SvgPathImage`` so the QR is a single ``<path>`` element —
    WeasyPrint renders it as true vector geometry, no rasterisation
    and no PIL dependency on the render path. Returns an ``<svg>``
    string safe to drop into the template via ``|safe``.
    """
    import qrcode
    import qrcode.image.svg

    qr = qrcode.QRCode(
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image()
    return img.to_string(encoding="unicode")


def _vat_breakdown_for_render(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert the stored string-Decimal breakdown to Decimal for render.

    The invoice's persisted ``vat_breakdown`` uses strings so it
    round-trips through JSON storage losslessly. The template needs
    real numbers so ``floatformat`` / locale-aware output works.
    """
    converted: list[dict[str, Any]] = []
    for row in rows:
        converted.append(
            {
                "rate": Decimal(row["rate"]),
                "subtotal": Decimal(row["subtotal"]),
                "vat": Decimal(row["vat"]),
                "gross": Decimal(row["gross"]),
            }
        )
    return converted


def _render_items(order: Order) -> list[dict[str, Any]]:
    """Flatten order items into a render-friendly structure.

    Django templates can't pass keyword arguments to methods, so the
    product name (via parler's ``safe_translation_getter``) has to be
    resolved here. Each row carries the exact numbers and labels the
    template needs — no more fragile ``|default`` chains against bound
    methods.
    """
    rows: list[dict[str, Any]] = []
    for item in order.items.select_related("product").all():
        product = item.product
        name = ""
        sku = ""
        if product is not None:
            name = (
                product.safe_translation_getter("name", any_language=True) or ""
            )
            sku = getattr(product, "sku", "") or ""
        rows.append(
            {
                "name": name,
                "sku": sku,
                "quantity": item.quantity,
                "unit_gross": Decimal(item.price.amount),
                "vat_rate": item.vat_rate,
                "line_total": Decimal(item.total_price.amount),
            }
        )
    return rows


def _build_context(
    order: Order,
    invoice: Invoice,
    vat_breakdown: list[dict[str, Any]],
    totals: dict[str, Decimal],
    *,
    specimen: bool = False,
) -> dict[str, Any]:
    """Assemble the template context dict.

    Kept separate from the render call so tests can assert exact
    content without going through WeasyPrint.

    QR precedence: when the invoice has been registered with myDATA,
    the QR encodes the **AADE-returned** ``qr_url`` (the legally
    verifiable one that deep-links into the ΑΑΔΕ portal). Otherwise
    we fall back to our own order-tracking URL so the PDF still has
    a useful scan target pre-transmission.

    A *specimen* carries no QR at all: it is registered nowhere, so there
    is nothing a scan could verify.
    """
    qr_target_url = ""
    if not specimen:
        qr_target_url = invoice.mydata_qr_url or _verification_url(
            order, language=get_order_language(order)
        )
    return {
        "invoice": invoice,
        "document_number": invoice.invoice_number,
        "is_credit": False,
        "order": order,
        "items": _render_items(order),
        "seller": invoice.seller_snapshot,
        "buyer": invoice.buyer_snapshot,
        # Rendering context uses Decimals so ``floatformat`` stays
        # locale-aware; persisted JSON keeps the string form.
        "vat_breakdown": _vat_breakdown_for_render(vat_breakdown),
        "totals": totals,
        "currency": invoice.currency,
        "pay_way_name": _pay_way_display(order),
        "verification_url": qr_target_url,
        "qr_svg": _build_qr_svg(qr_target_url) if qr_target_url else "",
        # Surfaced on the PDF when present so auditors / customers can
        # see the legal registration number next to the invoice number.
        "mydata_mark": invoice.mydata_mark,
        "specimen": specimen,
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
def _persist_invoice_row(
    order: Order, existing: Invoice | None
) -> tuple[Invoice, list[dict[str, Any]], dict[str, Any]]:
    """Allocate the number and persist the Invoice row (no PDF).

    Deliberately SHORT and transactional: the counter allocation takes
    a row lock, so anything slow inside this block (notably the PDF
    render) would hold both the lock and an idle transaction open —
    see ``generate_invoice`` for why that matters.
    """
    if existing is not None:
        # Preserve the original number + issue date so a regeneration
        # doesn't consume a fresh counter slot and leave the previous
        # one orphaned. The "corrective regen" use case is "the PDF is
        # wrong", not "this never happened" — and the same applies when
        # resuming a run whose render died after the row was committed.
        invoice = existing
        invoice_number = existing.invoice_number
        issue_date = existing.issue_date
        # Delete the stale PDF so the regenerated one lands at the
        # same storage key. Django's default ``get_available_name``
        # appends a suffix to avoid overwrites, which would leave
        # the pre-MARK PDF as an orphan (plus cost an extra S3
        # object per regeneration in prod).
        if invoice.document_file:
            invoice.document_file.delete(save=False)
    else:
        issue_date = timezone.localdate()
        invoice_number = InvoiceCounter.allocate(issue_date.year)
        invoice = Invoice(order=order)

    if existing is not None and existing.mydata_mark:
        # Once transmitted to AADE (has a MARK) the invoice's financial
        # values are LEGALLY frozen. A corrective PDF regen must reuse the
        # persisted breakdown/totals rather than recompute from the
        # possibly-changed order, or the PDF would disagree with the
        # AADE-registered invoice (G0263).
        vat_breakdown = existing.vat_breakdown
        # EVERY key ``_order_totals`` produces, or the PDF silently loses
        # rows: the template renders shipping/payment_fee/discount behind
        # ``{% if %}`` and then prints ``totals.total`` unconditionally.
        # Carrying only three keys printed a total that still contained
        # the shipping and the fee while their rows were gone, so a
        # legally registered document went out to the customer with
        # arithmetic that does not add up.
        #
        # Decimals, like ``_order_totals``: the row's columns are
        # ``MoneyField``s, and ``floatformat`` renders a ``Money`` as an
        # empty string, so the registered PDF printed blank shipping,
        # subtotal and TOTAL amounts (and a zero ``Money`` is truthy, so
        # an empty payment-fee row appeared too).
        totals = {
            "subtotal": existing.subtotal.amount,
            "total_vat": existing.total_vat.amount,
            "shipping": existing.shipping.amount,
            "payment_fee": existing.payment_fee.amount,
            "discount": existing.discount.amount,
            "total": existing.total.amount,
        }
    else:
        vat_breakdown = _compute_vat_breakdown(order)
        totals = _order_totals(order, vat_breakdown)
    currency = order.amount_due.currency.code

    invoice.invoice_number = invoice_number
    invoice.issue_date = issue_date
    invoice.seller_snapshot = _seller_snapshot()
    invoice.buyer_snapshot = _buyer_snapshot(order)
    invoice.vat_breakdown = vat_breakdown
    invoice.subtotal = totals["subtotal"]
    invoice.total_vat = totals["total_vat"]
    invoice.shipping = totals["shipping"]
    invoice.payment_fee = totals["payment_fee"]
    invoice.discount = totals["discount"]
    invoice.total = totals["total"]
    invoice.currency = currency
    # Save so ``invoice.pk`` exists when the upload_to callable builds
    # the file path during the (post-commit) render step.
    invoice.save()
    return invoice, vat_breakdown, totals


def generate_invoice(order: Order, *, force: bool = False) -> Invoice:
    """Idempotent invoice generation for a single order.

    Returns the existing Invoice if one exists WITH its PDF, unless
    ``force=True``. Never fabricates sequential numbers — always routes
    through :meth:`InvoiceCounter.allocate`. With ``force=True`` the
    original ``invoice_number`` and ``issue_date`` are preserved so the
    sequential register stays gap-free (Greek tax law forbids gaps) —
    only the PDF, snapshots, and derived totals are refreshed.

    The row write and the PDF render are deliberately split: every
    connection carries ``idle_in_transaction_session_timeout`` (10s,
    ``settings._db_options``) and a WeasyPrint render of a real invoice
    takes longer than that, so rendering inside the transaction had
    Postgres terminate the connection mid-render and NO invoice PDF was
    ever produced for any order. The row is committed first; the render
    then runs with no transaction open.

    A row committed by a run whose render then failed is resumed rather
    than returned as-is — otherwise the early return would hand back a
    PDF-less invoice forever.

    Issuing is where the platform's rule lives, because this is the one
    step that allocates a number: a call without ``force`` issues, so it
    is refused for an order :func:`receipt_issuable` rejects and while
    the store issues its receipts outside the platform, even when a row
    already exists (an old one would otherwise go on to AADE).
    ``force=True`` only re-renders an existing receipt and never creates
    one. Every refusal raises :class:`ReceiptNotIssuable`, whoever asked.
    """
    existing = Invoice.objects.filter(order_id=order.id).first()
    if force:
        # Re-rendering is for the receipt of a receipt order, whatever
        # status the order has moved on to; a row an invoice order kept
        # from before the rule is not a receipt to print again.
        if (
            existing is None
            or order.document_type != OrderDocumentTypeEnum.RECEIPT.value
        ):
            raise ReceiptNotIssuable(
                _("Order #%(order_id)s has no receipt to regenerate.")
                % {"order_id": order.id}
            )
    else:
        if not receipt_issuable(order):
            raise ReceiptNotIssuable(
                _(
                    "A retail receipt is issued for a receipt order once it "
                    "has shipped. Order #%(order_id)s is %(doc)s, status "
                    "%(status)s."
                )
                % {
                    "order_id": order.id,
                    "doc": order.get_document_type_display(),
                    "status": order.get_status_display(),
                }
            )
        if not load_mydata_config().is_ready():
            raise ReceiptNotIssuable(
                _(
                    "This store issues its receipts outside the platform, "
                    "so no receipt is created for order #%(order_id)s."
                )
                % {"order_id": order.id}
            )
        if existing is not None and existing.has_document():
            logger.debug(
                "Invoice %s already exists for order %s — returning existing",
                existing.invoice_number,
                order.id,
            )
            return existing

    invoice, vat_breakdown, totals = _persist_invoice_row(order, existing)

    # Render under the buyer's preferred language so e.g. a German
    # shopper gets a German-labelled invoice even though the seller
    # operates primarily in Greek. OUTSIDE the transaction — see above.
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


def render_specimen_pdf(order: Order) -> bytes:
    """Render the specimen («ΔΕΙΓΜΑ») of the order's retail receipt.

    The same template and context as the real receipt, built from an
    unsaved :class:`Invoice` and the live order, with a watermark and no
    QR. Nothing is persisted: no row, no :class:`InvoiceCounter` slot, no
    file, no myDATA call, so a specimen can neither be issued nor
    submitted. Refuses (:class:`SpecimenNotAvailable`) outside a demo
    store, whoever asks.
    """
    if not specimen_available(order):
        raise SpecimenNotAvailable(
            _("Order #%(order_id)s has no specimen receipt.")
            % {"order_id": order.id}
        )
    vat_breakdown = _compute_vat_breakdown(order)
    totals = _order_totals(order, vat_breakdown)
    with translation_override(get_order_language(order)):
        specimen = Invoice(
            order=order,
            invoice_number=_("SPECIMEN"),
            issue_date=timezone.localdate(),
            seller_snapshot=_seller_snapshot(),
            buyer_snapshot=_buyer_snapshot(order),
            currency=order.amount_due.currency.code,
        )
        return _render_pdf_bytes(
            _build_context(
                order, specimen, vat_breakdown, totals, specimen=True
            )
        )


def render_credit_pdf(credit: RetailCredit) -> RetailCredit:
    """Render (or re-render) the PDF of a credit document and store it.

    The totals and the VAT table are the ones frozen on the row, the
    parties are the receipt's snapshots, and the QR carries AADE's own URL
    once the document is registered. Like :func:`generate_invoice` the
    render runs with no transaction open, and the stale file is removed
    first so the replacement lands at the same storage key.
    """
    order = credit.order
    invoice = credit.invoice
    language = get_order_language(order)
    qr_target_url = credit.mydata_qr_url or _verification_url(
        order, language=language
    )
    context = {
        "invoice": credit,
        "document_number": credit.credit_number,
        "is_credit": True,
        "credited_invoice": invoice,
        "order": order,
        "items": [],
        "seller": invoice.seller_snapshot,
        "buyer": invoice.buyer_snapshot,
        "vat_breakdown": _vat_breakdown_for_render(credit.vat_breakdown),
        "totals": {
            "subtotal": credit.subtotal.amount,
            "total_vat": credit.total_vat.amount,
            "total": credit.total.amount,
        },
        "currency": credit.currency,
        "pay_way_name": _pay_way_display(order),
        "verification_url": qr_target_url,
        "qr_svg": _build_qr_svg(qr_target_url),
        "mydata_mark": credit.mydata_mark,
    }
    with translation_override(language):
        pdf_bytes = _render_pdf_bytes(context)
    if credit.document_file:
        credit.document_file.delete(save=False)
    credit.document_file.save(
        f"{credit.credit_number}.pdf", ContentFile(pdf_bytes), save=False
    )
    credit.save(update_fields=["document_file"])
    logger.info(
        "Rendered credit document #%s for order %s (%s bytes)",
        credit.pk,
        order.id,
        len(pdf_bytes),
    )
    return credit
