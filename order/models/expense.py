from __future__ import annotations

from typing import Any

from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField

from core.models import TimeStampMixinModel, UUIDModel
from order.enum.expense import ExpenseCategory
from tenant.staff_actors import StaffActor
from tenant.storage import TenantPrivateFileSystemStorage

#: What an invoice or receipt is scanned or photographed as.
EXPENSE_ATTACHMENT_EXTENSIONS = ("pdf", "png", "jpg", "jpeg")


def private_expense_storage() -> Any:
    """The per-tenant private storage for expense attachments.

    A callable for the reason ``order.models.invoice._private_invoice_storage``
    is one: ``FileField`` evaluates it once, with no tenant bound, so the
    location must be resolved per file operation, not here. The files are
    served only through ``ExpenseAdmin.attachment_download_view``.
    """
    return TenantPrivateFileSystemStorage()


def expense_attachment_upload_to(instance: Expense, filename: str) -> str:
    """``expenses/{YYYY}/{uuid}{ext}``: never the uploader's file name."""
    extension = (
        f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ""
    )
    return f"expenses/{instance.date:%Y}/{instance.uuid}{extension}"


class Expense(TimeStampMixinModel, UUIDModel):
    """Money the store spent that is not the cost of a product it sold.

    Entered by hand from invoices (a carrier's monthly invoice, an ad
    platform, rent). The profit report reads ``amount_net``: VAT on an
    expense is recoverable, so it is kept (``vat_amount``) for the
    accountant and never counted as a cost.
    """

    id = models.BigAutoField(primary_key=True)
    date = models.DateField(
        _("Date"),
        help_text=_(
            "The day the cost belongs to (the invoice date), which is the "
            "month of the profit report it lands in."
        ),
    )
    category = models.CharField(
        _("Category"), max_length=16, choices=ExpenseCategory.choices
    )
    description = models.CharField(_("Description"), max_length=255)
    amount_net = MoneyField(
        _("Amount (net of VAT)"),
        max_digits=11,
        decimal_places=2,
        help_text=_(
            "The cost without VAT: this is what the profit report uses."
        ),
    )
    vat_amount = MoneyField(
        _("VAT amount"),
        max_digits=11,
        decimal_places=2,
        default=0,
        help_text=_("The VAT on the invoice, kept for your accountant."),
    )
    attachment = models.FileField(
        _("Attachment"),
        storage=private_expense_storage,
        upload_to=expense_attachment_upload_to,
        validators=[
            FileExtensionValidator(
                allowed_extensions=EXPENSE_ATTACHMENT_EXTENSIONS
            )
        ],
        blank=True,
        help_text=_("The invoice or receipt (PDF or image). Kept private."),
    )
    created_by_id = models.BigIntegerField(
        _("Created By"),
        null=True,
        blank=True,
        help_text=_(
            "Staff member who entered the expense. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor."
        ),
    )
    created_by = StaffActor()

    class Meta(TypedModelMeta):
        verbose_name = _("Expense")
        verbose_name_plural = _("Expenses")
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(
                fields=["date", "category"], name="expense_date_cat_ix"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_net__gt=0),
                name="expense_amount_net_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(vat_amount__gte=0),
                name="expense_vat_amount_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return gettext("%(category)s %(amount)s on %(date)s") % {
            "category": self.get_category_display(),
            "amount": self.amount_net,
            "date": self.date,
        }

    def has_attachment(self) -> bool:
        return bool(self.attachment and self.attachment.name)
