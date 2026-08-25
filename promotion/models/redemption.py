from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField

from core.models import TimeStampMixinModel


class PromotionRedemption(TimeStampMixinModel):
    """One promotion applied to one order — the usage-limit ledger.

    Rows are written inside the order-create transaction while the
    Promotion row is locked, so counting them under that same lock is
    race-free limit enforcement.
    """

    id = models.BigAutoField(primary_key=True)
    promotion = models.ForeignKey(
        "promotion.Promotion",
        related_name="redemptions",
        on_delete=models.PROTECT,
    )
    code = models.ForeignKey(
        "promotion.PromotionCode",
        related_name="redemptions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    order = models.ForeignKey(
        "order.Order",
        related_name="promotion_redemptions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="promotion_redemptions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    email = models.EmailField(
        _("Email"),
        blank=True,
        default="",
        help_text=_("Checkout email — used for guest per-customer limits"),
    )
    amount = MoneyField(_("Amount"), max_digits=11, decimal_places=2)

    class Meta(TypedModelMeta):
        verbose_name = _("Promotion Redemption")
        verbose_name_plural = _("Promotion Redemptions")
        ordering = ["-created_at"]
        db_table = "promotion_redemption"
        constraints = [
            models.UniqueConstraint(
                fields=["promotion", "order"],
                name="unique_promotion_per_order",
            ),
        ]
        indexes = [
            # Explicit names: the mixin's %(class)s naming exceeds the
            # 32-char index-name cap for this class name (models.E034).
            BTreeIndex(
                fields=["created_at"], name="promo_redeem_created_ix"
            ),
            BTreeIndex(
                fields=["updated_at"], name="promo_redeem_updated_ix"
            ),
            BTreeIndex(
                fields=["promotion", "user"], name="promo_redeem_user_ix"
            ),
            BTreeIndex(
                fields=["promotion", "email"], name="promo_redeem_email_ix"
            ),
        ]

    def __str__(self):
        return f"{self.promotion_id} on order {self.order_id}: {self.amount}"
