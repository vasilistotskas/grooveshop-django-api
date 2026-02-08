from decimal import Decimal

from django.contrib.postgres.indexes import BTreeIndex
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFieldsModel

from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from loyalty.managers.tier import LoyaltyTierManager


class LoyaltyTier(
    TranslatableModel, TimeStampMixinModel, UUIDModel, SortableModel
):
    id = models.BigAutoField(primary_key=True)
    required_level = models.PositiveIntegerField(
        _("Required Level"),
        unique=True,
        help_text=_("Minimum level to achieve this tier"),
    )
    points_multiplier = models.DecimalField(
        _("Points Multiplier"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("1.0"),
        validators=[MinValueValidator(Decimal("1.0"))],
        help_text=_(
            "Multiplier applied to earned points for users in this tier"
        ),
    )

    objects: LoyaltyTierManager = LoyaltyTierManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Loyalty Tier")
        verbose_name_plural = _("Loyalty Tiers")
        ordering = ["required_level"]
        db_table = "loyalty_tier"
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(
                fields=["required_level"], name="loyalty_tier_req_lvl_ix"
            ),
        ]

    def __str__(self):
        return (
            self.safe_translation_getter("name")
            or f"Tier (level {self.required_level})"
        )


class LoyaltyTierTranslation(TranslatedFieldsModel):
    master = models.ForeignKey(
        LoyaltyTier,
        related_name="translations",
        on_delete=models.CASCADE,
    )
    name = models.CharField(_("Name"), max_length=100)
    description = models.TextField(_("Description"), blank=True, default="")

    class Meta:
        unique_together = [("language_code", "master")]
        db_table = "loyalty_tier_translation"

    def __str__(self):
        return self.name
