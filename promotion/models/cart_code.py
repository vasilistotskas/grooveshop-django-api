from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel


class CartPromotionCode(TimeStampMixinModel):
    """A coupon code the shopper attached to their cart.

    The schema allows several rows per cart so multi-code checkout can
    ship later without a migration, but ``CouponService.apply`` keeps
    at most one row per cart in v1.
    """

    id = models.BigAutoField(primary_key=True)
    cart = models.ForeignKey(
        "cart.Cart",
        related_name="applied_codes",
        on_delete=models.CASCADE,
    )
    code = models.ForeignKey(
        "promotion.PromotionCode",
        related_name="cart_applications",
        on_delete=models.PROTECT,
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Cart Promotion Code")
        verbose_name_plural = _("Cart Promotion Codes")
        ordering = ["-created_at"]
        db_table = "promotion_cart_code"
        indexes = [
            # The list orders by created_at and `date_hierarchy`
            # range-scans it. updated_at is never sorted or filtered, so
            # it stays unindexed rather than costing a write per row.
            BTreeIndex(
                fields=["created_at"], name="cartpromotioncode_created_at_ix"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "code"],
                name="unique_code_per_cart",
            ),
        ]

    def __str__(self):
        return f"{self.code.code} on cart {self.cart_id}"
