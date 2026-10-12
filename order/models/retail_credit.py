"""The retail credit document (myDATA 11.4) the platform issues for a refund.

ΕΛΠ (law 4308/2014) art. 12(4): "in the case of discounts or refunds a
retail credit document is issued". The platform's retail receipt is the
order's :class:`~order.models.invoice.Invoice` (11.1); every refund of an
order whose receipt is registered with AADE gets a ``RetailCredit`` linked
to that receipt's MARK, crediting exactly the amount refunded.
"""

from __future__ import annotations

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField

from core.models import TimeStampMixinModel, UUIDModel
from order.models.invoice import (
    MyDataRegistration,
    _private_invoice_storage,
)

CREDIT_NUMBER_FORMAT = "CRD-{year}-{number:06d}"


class CreditReason(models.TextChoices):
    """Why a credit document exists."""

    REFUND = "REFUND", _("Refund")
    SETTLEMENT = "SETTLEMENT", _("Order closed without a sale")


def _credit_upload_to(instance: RetailCredit, filename: str) -> str:
    """``invoices/{year}/{credit_number}.pdf``.

    Under ``invoices/`` on purpose: tenant offboarding keeps that
    directory for the legal retention period and erases the rest of the
    private tree, and a credit document is a tax record like the receipt.
    """
    year = (
        instance.issue_date.year if instance.issue_date else timezone.now().year
    )
    base = instance.credit_number or f"pending-{instance.pk or 'new'}"
    return f"invoices/{year}/{base}.pdf"


class CreditCounter(models.Model):
    """One row per fiscal year; the credit documents' own gapless sequence.

    Separate from :class:`~order.models.invoice.InvoiceCounter` because
    myDATA's ``aa`` must be gapless within a series, and the receipts and
    their credits are different series.
    """

    year = models.PositiveIntegerField(_("Year"), unique=True)
    next_number = models.PositiveIntegerField(_("Next Number"), default=1)

    class Meta(TypedModelMeta):
        verbose_name = _("Credit Counter")
        verbose_name_plural = _("Credit Counters")

    def __str__(self) -> str:
        return gettext("%(year)s: next number %(number)s") % {
            "year": self.year,
            "number": self.next_number,
        }

    @classmethod
    def allocate(cls, year: int) -> str:
        """Atomically reserve the next credit number for ``year``."""
        with transaction.atomic():
            counter, _created = cls.objects.select_for_update().get_or_create(
                year=year, defaults={"next_number": 1}
            )
            number = counter.next_number
            counter.next_number = number + 1
            counter.save(update_fields=["next_number"])
        return CREDIT_NUMBER_FORMAT.format(year=year, number=number)


class RetailCredit(TimeStampMixinModel, UUIDModel, MyDataRegistration):
    """A retail credit document (11.4) for the order's receipt."""

    order = models.ForeignKey(
        "order.Order",
        related_name="credit_documents",
        on_delete=models.PROTECT,
        verbose_name=_("Order"),
    )
    invoice = models.ForeignKey(
        "order.Invoice",
        related_name="credits",
        on_delete=models.PROTECT,
        verbose_name=_("Credited receipt"),
        help_text=_(
            "The retail receipt (11.1) this document credits. AADE does "
            "not link an 11.4 to its receipt, so the link is kept here only."
        ),
    )
    reason = models.CharField(
        _("Reason"),
        max_length=16,
        choices=CreditReason.choices,
        default=CreditReason.REFUND,
    )
    refund = models.OneToOneField(
        "order.PaymentTransaction",
        related_name="credit_document",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        verbose_name=_("Refund"),
        help_text=_(
            "The ledger refund this document credits. One document per "
            "refund; empty for the settlement of a closed order."
        ),
    )
    credit_number = models.CharField(
        _("Credit Number"),
        max_length=32,
        unique=True,
        help_text=_(
            "Sequential identifier in the form ``CRD-{YEAR}-{NNNNNN}``."
        ),
    )
    issue_date = models.DateField(_("Issue Date"), default=timezone.localdate)
    document_file = models.FileField(
        _("Document File"),
        upload_to=_credit_upload_to,
        storage=_private_invoice_storage,
        blank=True,
        null=True,
    )
    subtotal = MoneyField(
        _("Subtotal (excl. VAT)"), max_digits=11, decimal_places=2, default=0
    )
    total_vat = MoneyField(
        _("Total VAT"), max_digits=11, decimal_places=2, default=0
    )
    total = MoneyField(
        _("Total (incl. VAT)"), max_digits=11, decimal_places=2, default=0
    )
    vat_breakdown = models.JSONField(
        _("VAT Breakdown"),
        default=list,
        help_text=_(
            "``{rate, subtotal, vat, gross}`` rows of what this document "
            "credits, frozen when it was issued."
        ),
    )
    currency = models.CharField(_("Currency"), max_length=10, default="EUR")

    class Meta(TypedModelMeta):
        verbose_name = _("Retail Credit Document")
        verbose_name_plural = _("Retail Credit Documents")
        ordering = ["-issue_date", "-credit_number"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["mydata_status"], name="credit_mydata_status_ix"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["order"],
                condition=models.Q(reason=CreditReason.SETTLEMENT),
                name="credit_one_settlement_per_order",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(reason=CreditReason.REFUND)
                    & models.Q(refund__isnull=False)
                )
                | (
                    models.Q(reason=CreditReason.SETTLEMENT)
                    & models.Q(refund__isnull=True)
                ),
                name="credit_refund_iff_reason_refund",
            ),
        ]

    def __str__(self) -> str:
        return self.credit_number

    @property
    def document_number(self) -> str:
        return self.credit_number

    def has_document(self) -> bool:
        """True when the PDF has been generated and stored."""
        return bool(self.document_file and self.document_file.name)
