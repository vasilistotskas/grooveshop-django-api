from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel, UUIDModel
from loyalty.enum import TransactionType
from loyalty.managers.transaction import PointsTransactionManager
from tenant.staff_actors import StaffActor


class PointsTransaction(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="points_transactions",
        on_delete=models.CASCADE,
        verbose_name=_("User"),
    )
    points = models.IntegerField(
        _("Points"),
        help_text=_(
            "Positive for earn/bonus, negative for redeem/expire/adjust"
        ),
    )
    transaction_type = models.CharField(
        _("Transaction Type"),
        max_length=10,
        choices=TransactionType.choices,
    )
    reference_order = models.ForeignKey(
        "order.Order",
        related_name="points_transactions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Reference Order"),
    )
    description = models.TextField(_("Description"), blank=True, default="")
    refund_attempt = models.ForeignKey(
        "order.RefundAttempt",
        related_name="points_transactions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_index=False,
        verbose_name=_("Refund attempt"),
        help_text=_(
            "The refund that gave back points the customer had spent, when "
            "this row is one."
        ),
    )
    created_by_id = models.BigIntegerField(
        _("Created By"),
        null=True,
        blank=True,
        help_text=_(
            "Admin who created this adjustment. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor."
        ),
    )
    created_by = StaffActor()

    objects: PointsTransactionManager = PointsTransactionManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Points Transaction")
        verbose_name_plural = _("Points Transactions")
        ordering = ["-created_at"]
        db_table = "loyalty_points_transaction"
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["user"], name="loyalty_tx_user_ix"),
            BTreeIndex(fields=["transaction_type"], name="loyalty_tx_type_ix"),
            BTreeIndex(fields=["reference_order"], name="loyalty_tx_order_ix"),
            BTreeIndex(
                fields=["user", "transaction_type"],
                name="loyalty_tx_user_type_ix",
            ),
        ]

    def __str__(self):
        return ngettext(
            "%(type)s %(points)s point (user %(user)s)",
            "%(type)s %(points)s points (user %(user)s)",
            abs(self.points),
        ) % {
            "type": self.get_transaction_type_display(),
            "points": self.points,
            "user": self.user_id,
        }
