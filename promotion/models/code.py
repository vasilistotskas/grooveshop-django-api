from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel


class PromotionCode(TimeStampMixinModel):
    id = models.BigAutoField(primary_key=True)
    promotion = models.ForeignKey(
        "promotion.Promotion",
        related_name="codes",
        on_delete=models.CASCADE,
    )
    code = models.CharField(
        _("Code"),
        max_length=40,
        unique=True,
        help_text=_("Case-insensitive; stored uppercase"),
    )
    usage_limit = models.PositiveIntegerField(
        _("Usage Limit"),
        null=True,
        blank=True,
        help_text=_(
            "Maximum number of orders that may use this specific code "
            "(1 for single-use bulk codes); empty means unlimited"
        ),
    )
    assigned_to = models.ForeignKey(
        "user.UserAccount",
        related_name="assigned_promotion_codes",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=_(
            "When set, only this customer can redeem the code (personal coupon)"
        ),
    )
    assigned_to_email = models.EmailField(
        _("Assigned To Email"),
        blank=True,
        default="",
        help_text=_(
            "When set, only checkouts with this email can redeem the "
            "code — covers guests without an account"
        ),
    )
    is_active = models.BooleanField(_("Active"), default=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Promotion Code")
        verbose_name_plural = _("Promotion Codes")
        ordering = ["-created_at"]
        db_table = "promotion_code"
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["promotion"], name="promotion_code_promo_ix"),
        ]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)
