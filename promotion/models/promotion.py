from decimal import Decimal

from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from parler.fields import TranslationsForeignKey
from parler.models import TranslatableModel, TranslatedFieldsModel

from core.models import TimeStampMixinModel, UUIDModel
from promotion.enum import BenefitType, PromotionTrigger, TargetScope
from promotion.managers.promotion import PromotionManager


class Promotion(TranslatableModel, TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    trigger = models.CharField(
        _("Trigger"),
        max_length=10,
        choices=PromotionTrigger,
        default=PromotionTrigger.CODE,
        help_text=_(
            "Automatic promotions apply to every eligible cart; "
            "code promotions require the shopper to enter a coupon code"
        ),
    )
    benefit_type = models.CharField(
        _("Benefit Type"),
        max_length=20,
        choices=BenefitType,
        default=BenefitType.PERCENTAGE,
    )
    benefit_value = models.DecimalField(
        _("Benefit Value"),
        max_digits=11,
        decimal_places=2,
        default=Decimal("0.0"),
        help_text=_(
            "Percent (0-100) for percentage benefits, EUR amount for "
            "fixed-amount benefits; ignored for free shipping"
        ),
    )
    target_scope = models.CharField(
        _("Target Scope"),
        max_length=10,
        choices=TargetScope,
        default=TargetScope.ORDER,
    )
    products = models.ManyToManyField(
        "product.Product",
        related_name="promotions",
        blank=True,
        help_text=_("Only used when target scope is 'Specific products'"),
    )
    categories = models.ManyToManyField(
        "product.ProductCategory",
        related_name="promotions",
        blank=True,
        help_text=_(
            "Only used when target scope is 'Specific categories'; "
            "subcategories are included automatically"
        ),
    )
    min_subtotal = MoneyField(
        _("Minimum Subtotal"),
        max_digits=11,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_(
            "Cart items total (incl. VAT) required for the promotion to apply"
        ),
    )
    first_order_only = models.BooleanField(
        _("First Order Only"),
        default=False,
        help_text=_(
            "Apply only to customers with no previous orders. For guests "
            "this is checked against the checkout email and is best-effort."
        ),
    )
    is_active = models.BooleanField(_("Active"), default=False)
    starts_at = models.DateTimeField(_("Starts At"), null=True, blank=True)
    ends_at = models.DateTimeField(_("Ends At"), null=True, blank=True)
    stackable = models.BooleanField(
        _("Stackable"),
        default=False,
        help_text=_(
            "Stackable promotions combine with each other; a "
            "non-stackable promotion applies alone and only when it "
            "beats the combined stackable discount. Ignored for free "
            "shipping, which always combines."
        ),
    )
    priority = models.PositiveIntegerField(
        _("Priority"),
        default=0,
        help_text=_("Lower numbers apply first among stackable promotions"),
    )
    max_discount_amount = MoneyField(
        _("Maximum Discount Amount"),
        max_digits=11,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Ceiling for the discount of a single application"),
    )
    usage_limit_total = models.PositiveIntegerField(
        _("Total Usage Limit"),
        null=True,
        blank=True,
        help_text=_("Maximum number of orders that may use this promotion"),
    )
    usage_limit_per_customer = models.PositiveIntegerField(
        _("Per-customer Usage Limit"),
        null=True,
        blank=True,
    )

    objects: PromotionManager = PromotionManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Promotion")
        verbose_name_plural = _("Promotions")
        ordering = ["-created_at"]
        db_table = "promotion"
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["is_active", "starts_at", "ends_at"],
                name="promotion_live_ix",
            ),
            BTreeIndex(fields=["trigger"], name="promotion_trigger_ix"),
        ]

    def __str__(self):
        return (
            self.safe_translation_getter("name")
            or f"Promotion {self.pk or ''}".strip()
        )

    def clean(self):
        super().clean()
        if self.benefit_type == BenefitType.PERCENTAGE and not (
            Decimal("0") < self.benefit_value <= Decimal("100")
        ):
            raise ValidationError(
                {
                    "benefit_value": _(
                        "Percentage benefits require a value between 0 and 100."
                    )
                }
            )
        if (
            self.benefit_type == BenefitType.FIXED_AMOUNT
            and self.benefit_value <= 0
        ):
            raise ValidationError(
                {
                    "benefit_value": _(
                        "Fixed-amount benefits require a positive value."
                    )
                }
            )
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValidationError({"ends_at": _("End must be after start.")})

    @property
    def is_live(self) -> bool:
        """Active and inside the schedule window right now."""
        if not self.is_active:
            return False
        now = timezone.now()
        if self.starts_at and self.starts_at > now:
            return False
        return not (self.ends_at and self.ends_at <= now)


class PromotionTranslation(TranslatedFieldsModel):
    master = TranslationsForeignKey(
        Promotion,
        related_name="translations",
        on_delete=models.CASCADE,
    )
    name = models.CharField(_("Name"), max_length=255)
    description = models.TextField(_("Description"), blank=True, default="")

    class Meta:
        unique_together = [("language_code", "master")]
        db_table = "promotion_translation"

    def __str__(self):
        return self.name
