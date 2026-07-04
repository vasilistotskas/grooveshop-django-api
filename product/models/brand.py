from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel


class Brand(TimeStampMixinModel):
    """
    Product manufacturer/brand registry — one canonical name per brand,
    consumed by the Meta/TikTok catalog feeds and storefront display.

    Deliberately not translatable: brand names are proper nouns.
    """

    id = models.BigAutoField(primary_key=True)
    name = models.CharField(_("Name"), max_length=255, unique=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Brand")
        verbose_name_plural = _("Brands")
        ordering = ["name"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
        ]

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"<Brand: {self.name} ({self.pk})>"
