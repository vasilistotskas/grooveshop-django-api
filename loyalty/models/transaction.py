from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel, UUIDModel
from loyalty.enum import TransactionType
from loyalty.managers.transaction import PointsTransactionManager


class PointsTransaction(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="points_transactions",
        on_delete=models.CASCADE,
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
        choices=TransactionType,
    )
    reference_order = models.ForeignKey(
        "order.Order",
        related_name="points_transactions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    description = models.TextField(_("Description"), blank=True, default="")
    created_by = models.ForeignKey(
        "user.UserAccount",
        related_name="points_adjustments_made",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=_("Admin user who created this adjustment"),
    )

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
        return f"{self.get_transaction_type_display()} {self.points} pts (user {self.user_id})"
