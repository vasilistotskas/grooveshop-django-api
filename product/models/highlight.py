from __future__ import annotations

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import SortableModel, TimeStampMixinModel
from product.managers.highlight import ProductHighlightManager

#: Google Merchant Center's limit for one ``product_highlight``
#: (support.google.com/merchants/answer/9216100).
HIGHLIGHT_MAX_LENGTH = 150


class ProductHighlight(TranslatableModel, TimeStampMixinModel, SortableModel):
    """One short benefit of a product, published as a Google Merchant
    ``product_highlight``. A product has a list of them, in the order the
    merchant sets; each is written per language."""

    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        "product.Product",
        related_name="highlights",
        on_delete=models.CASCADE,
        verbose_name=_("Product"),
    )
    translations = TranslatedFields(
        text=models.CharField(
            _("Highlight"),
            max_length=HIGHLIGHT_MAX_LENGTH,
            help_text=_(
                "Google Shopping shows these as the product's selling points. "
                "Add 4 to 6 short benefits (at least 2, or Google ignores "
                "them). Do not put in the price, shipping, a date, the "
                "store's name or a link, do not write in capitals, and do "
                "not repeat the title or the description."
            ),
        )
    )

    objects: ProductHighlightManager = ProductHighlightManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Product Highlight")
        verbose_name_plural = _("Product Highlights")
        ordering = ["sort_order", "id"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(
                fields=["product", "sort_order"],
                name="prod_highlight_prod_sort_ix",
            ),
        ]

    def __str__(self):
        return self.safe_translation_getter("text", any_language=True) or ""

    def get_ordering_queryset(self):
        return self.product.highlights.all()
