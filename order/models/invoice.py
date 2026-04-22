"""Invoice model with atomic per-fiscal-year sequential numbering.

Greek tax law (like most EU jurisdictions) requires invoices to be
numbered sequentially with no gaps within each fiscal year. This module
provides:

- :class:`InvoiceCounter`: a single-row-per-year allocation table that
  uses ``select_for_update`` to hand out the next number atomically,
  even under concurrent invoice creation. The counter is the only
  source of truth — callers should never derive numbers from
  ``Invoice.objects.count()``.

- :class:`Invoice`: a read-mostly archive row linked 1:1 to an Order.
  Holds the rendered PDF (private storage, never publicly accessible),
  a cached ``vat_breakdown`` so re-rendering a 2022 invoice in 2026
  yields the same VAT table even if current rates changed, and
  ``seller_snapshot`` / ``buyer_snapshot`` so the invoice captures the
  parties at the time of issuance (tax law requires the invoice to
  reflect reality at issue time, not "latest" edits).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.core.files.storage import FileSystemStorage
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField

from core.models import TimeStampMixinModel, UUIDModel

if TYPE_CHECKING:
    pass


INVOICE_NUMBER_FORMAT = "INV-{year}-{number:06d}"


class MyDataStatus(models.TextChoices):
    """State of the invoice's myDATA (IAPR/ΑΑΔΕ) submission lifecycle.

    Mirrors the AADE response-state conventions:
    - ``NOT_SENT``: initial / myDATA disabled / never attempted.
    - ``PENDING``: queued for submission (task scheduled).
    - ``SUBMITTED``: request sent, awaiting AADE response.
    - ``CONFIRMED``: MARK assigned, PDF regenerated with MARK + QR.
    - ``REJECTED``: AADE returned a terminal ``ValidationError`` /
      ``XMLSyntaxError`` — customer PDF is still the pre-transmission
      version and ops must intervene (fix master data, regenerate).
    - ``CANCELED``: a ``CancelInvoice`` succeeded; ``mydata_cancellation_mark``
      holds the 9-series MARK from AADE.
    """

    NOT_SENT = "NOT_SENT", _("Not sent")
    PENDING = "PENDING", _("Pending submission")
    SUBMITTED = "SUBMITTED", _("Submitted")
    CONFIRMED = "CONFIRMED", _("Confirmed (MARK assigned)")
    REJECTED = "REJECTED", _("Rejected")
    CANCELED = "CANCELED", _("Canceled in myDATA")


def _private_invoice_storage() -> Any:
    """Resolve the private storage backend for invoice files.

    On AWS-backed deployments this is the ``PrivateMediaStorage``
    subclass (``default_acl='private'`` + ``custom_domain=False`` so
    downloads require a signed URL). In DEBUG / self-hosted static
    setups it falls back to a filesystem directory — still separate
    from the public ``media/`` tree so a misconfigured webserver won't
    accidentally serve invoices.
    """
    if getattr(settings, "USE_AWS", False):
        from core.storages import PrivateMediaStorage

        return PrivateMediaStorage()
    location = getattr(
        settings,
        "PRIVATE_MEDIA_ROOT",
        settings.MEDIA_ROOT + "_private"
        if getattr(settings, "MEDIA_ROOT", None)
        else "private_media",
    )
    return FileSystemStorage(location=location)


def _invoice_upload_to(instance: Invoice, filename: str) -> str:
    """Path under private storage: ``invoices/{year}/{invoice_number}.pdf``.

    Keeps invoices partitioned per year so archival (or later GDPR
    scrubbing of anonymised buyer data) can operate on a stable prefix.
    """
    year = (
        instance.issue_date.year if instance.issue_date else timezone.now().year
    )
    base = instance.invoice_number or f"pending-{instance.pk or 'new'}"
    return f"invoices/{year}/{base}.pdf"


class InvoiceCounter(models.Model):
    """One row per fiscal year; ``next_number`` is incremented atomically.

    This is intentionally NOT the same as ``Invoice.objects.count() + 1``
    because deletions/gaps would otherwise skew the sequence. Having a
    dedicated counter also lets ops bump the starting number (e.g.
    reserving ``INV-2026-000001..100`` for legacy imports) by editing
    one row rather than faking Invoice rows.
    """

    year = models.PositiveIntegerField(_("Year"), unique=True)
    next_number = models.PositiveIntegerField(_("Next Number"), default=1)

    class Meta(TypedModelMeta):
        verbose_name = _("Invoice Counter")
        verbose_name_plural = _("Invoice Counters")

    def __str__(self) -> str:
        return f"{self.year}: next={self.next_number}"

    @classmethod
    def allocate(cls, year: int) -> str:
        """Atomically reserve the next invoice number for ``year``.

        Uses ``select_for_update`` so concurrent transactions block on
        the row lock rather than racing. The returned string follows
        ``INVOICE_NUMBER_FORMAT`` so callers don't need to format it
        themselves.
        """
        with transaction.atomic():
            counter, _created = cls.objects.select_for_update().get_or_create(
                year=year, defaults={"next_number": 1}
            )
            number = counter.next_number
            counter.next_number = number + 1
            counter.save(update_fields=["next_number"])
        return INVOICE_NUMBER_FORMAT.format(year=year, number=number)


class Invoice(TimeStampMixinModel, UUIDModel):
    """Immutable-by-convention invoice archive linked to an Order.

    Once ``document_file`` is populated we treat this row as final —
    regenerating requires deleting the Invoice and its counter entry,
    which breaks sequential numbering and must be done deliberately.
    """

    order = models.OneToOneField(
        "order.Order",
        related_name="invoice",
        on_delete=models.PROTECT,
    )
    invoice_number = models.CharField(
        _("Invoice Number"),
        max_length=32,
        unique=True,
        help_text=_(
            "Sequential identifier in the form ``INV-{YEAR}-{NNNNNN}``. "
            "Gaps are not allowed by Greek tax law."
        ),
    )
    issue_date = models.DateField(
        _("Issue Date"),
        default=timezone.localdate,
        help_text=_(
            "Fiscal date of issue. Immutable once the invoice is "
            "rendered — used for sequential numbering and reporting."
        ),
    )
    document_file = models.FileField(
        _("Document File"),
        upload_to=_invoice_upload_to,
        storage=_private_invoice_storage,
        blank=True,
        null=True,
        help_text=_(
            "The rendered PDF. Stored in private storage — customer "
            "access goes through ``OrderViewSet.invoice_download`` "
            "(streamed through Django auth); direct storage URLs are "
            "signed via ``PrivateMediaStorage`` as a defence in depth."
        ),
    )
    subtotal = MoneyField(
        _("Subtotal (excl. VAT)"),
        max_digits=11,
        decimal_places=2,
        default=0,
    )
    total_vat = MoneyField(
        _("Total VAT"),
        max_digits=11,
        decimal_places=2,
        default=0,
    )
    total = MoneyField(
        _("Total (incl. VAT)"),
        max_digits=11,
        decimal_places=2,
        default=0,
    )
    vat_breakdown = models.JSONField(
        _("VAT Breakdown"),
        default=list,
        help_text=_(
            "Cached list of ``{rate, subtotal, vat, gross}`` rows — "
            "frozen at issue time so re-rendering the invoice always "
            "yields the same VAT table even if product rates change."
        ),
    )
    seller_snapshot = models.JSONField(
        _("Seller Snapshot"),
        default=dict,
        help_text=_("Seller name / VAT ID / address captured at issue time."),
    )
    buyer_snapshot = models.JSONField(
        _("Buyer Snapshot"),
        default=dict,
        help_text=_(
            "Buyer name / email / billing address captured at issue time."
        ),
    )
    currency = models.CharField(
        _("Currency"),
        max_length=10,
        default=settings.DEFAULT_CURRENCY,
    )

    # ── myDATA (IAPR / ΑΑΔΕ) fields ──────────────────────────────────
    # Populated after successful transmission to AADE. Nullable/blank
    # defaults so pre-myDATA invoices migrate without touching the
    # sequential register. All fields are read-only — write paths live
    # exclusively in ``order.mydata`` (submit / cancel).
    mydata_status = models.CharField(
        _("myDATA Status"),
        max_length=20,
        choices=MyDataStatus,
        default=MyDataStatus.NOT_SENT,
        help_text=_(
            "Lifecycle state of the invoice's AADE myDATA submission. "
            "NOT_SENT is the resting state when the myDATA integration "
            "is disabled or the invoice predates it."
        ),
    )
    mydata_invoice_type = models.CharField(
        _("myDATA Invoice Type"),
        max_length=10,
        blank=True,
        default="",
        help_text=_(
            "AADE invoiceType code — e.g. 11.1 (B2C retail), 1.1 (B2B "
            "sales invoice), 5.1 (linked credit note)."
        ),
    )
    mydata_series = models.CharField(
        _("myDATA Series"),
        max_length=50,
        blank=True,
        default="",
        help_text=_(
            "AADE ``series`` field — the alphanumeric part of the "
            "document's unique identifier within the issuer's register."
        ),
    )
    mydata_aa = models.PositiveIntegerField(
        _("myDATA Serial Number (aa)"),
        null=True,
        blank=True,
        help_text=_(
            "AADE ``aa`` field — strictly-increasing integer within "
            "``(series, year)``. Must be gapless per Greek tax law."
        ),
    )
    mydata_uid = models.CharField(
        _("myDATA UID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Deterministic SHA-1 fingerprint of (issuer VAT | year | "
            "branch | invoiceType | series | aa). Sent as the request's "
            "``uid`` so retries dedupe against AADE error 228."
        ),
    )
    mydata_mark = models.BigIntegerField(
        _("myDATA MARK"),
        null=True,
        blank=True,
        unique=True,
        help_text=_(
            "15-digit registration number assigned by AADE. This is "
            "the legally-authoritative identifier — printed on the PDF."
        ),
    )
    mydata_authentication_code = models.CharField(
        _("myDATA Authentication Code"),
        max_length=128,
        blank=True,
        default="",
        help_text=_(
            "Short hash AADE generates alongside the MARK; part of the "
            "``qr_url`` query string. Populated only via licensed "
            "provider channels (kept for forward compatibility)."
        ),
    )
    mydata_qr_url = models.URLField(
        _("myDATA QR URL"),
        max_length=500,
        blank=True,
        default="",
        help_text=_(
            "AADE-returned URL for the PDF QR code. NEVER synthesise — "
            "AADE changes the query-string parameters between schema "
            "versions; always store the string they return verbatim."
        ),
    )
    mydata_cancellation_mark = models.BigIntegerField(
        _("myDATA Cancellation MARK"),
        null=True,
        blank=True,
        help_text=_(
            "9-series MARK returned by a successful ``CancelInvoice`` "
            "call — proves the cancellation registered."
        ),
    )
    mydata_error_code = models.CharField(
        _("myDATA Error Code"),
        max_length=10,
        blank=True,
        default="",
        help_text=_(
            "Numeric AADE ``errorCode`` from the most recent failed "
            "submission (e.g. 101 XML syntax, 102 inactive VAT, "
            "216/217 VAT category). Empty when last attempt succeeded."
        ),
    )
    mydata_error_message = models.TextField(
        _("myDATA Error Message"),
        blank=True,
        default="",
        help_text=_("Human-readable message that accompanied the error code."),
    )
    mydata_submitted_at = models.DateTimeField(
        _("myDATA Submitted At"),
        null=True,
        blank=True,
        help_text=_(
            "Timestamp of the first transmission attempt — preserved "
            "across retries so reporting reflects the original send time."
        ),
    )
    mydata_confirmed_at = models.DateTimeField(
        _("myDATA Confirmed At"),
        null=True,
        blank=True,
        help_text=_("Timestamp AADE assigned the MARK."),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Invoice")
        verbose_name_plural = _("Invoices")
        ordering = ["-issue_date", "-invoice_number"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["issue_date"], name="invoice_issue_date_ix"),
            BTreeIndex(
                fields=["invoice_number"], name="invoice_invoice_number_ix"
            ),
            BTreeIndex(
                fields=["mydata_status"], name="invoice_mydata_status_ix"
            ),
        ]

    def __str__(self) -> str:
        return self.invoice_number

    @property
    def order_id_display(self) -> int | None:
        return self.order_id

    def has_document(self) -> bool:
        """True when the PDF has been generated and stored."""
        return bool(self.document_file and self.document_file.name)

    def has_mydata_mark(self) -> bool:
        """True when AADE has assigned a MARK — i.e. the invoice is
        legally registered and the PDF should embed MARK + QR."""
        return bool(self.mydata_mark) and bool(self.mydata_qr_url)
