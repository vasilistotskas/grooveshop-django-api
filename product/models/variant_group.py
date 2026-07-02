from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import TimeStampMixinModel, UUIDModel


class ProductVariantGroup(TranslatableModel, TimeStampMixinModel, UUIDModel):
    """
    Groups sibling products that are the same item in different variations
    (e.g. the same cable clip in five colours, or a phone in several memory
    sizes).

    Each member stays a full :class:`~product.models.product.Product` — it
    keeps its own price, stock, images, slug, SEO and cart/order references.
    This model is purely an identity that the siblings point at via
    ``Product.variant_group`` so the storefront can render variant selectors.
    The variant *axes* themselves (Colour, Memory, …) are modelled with the
    existing ``Attribute`` / ``AttributeValue`` / ``ProductAttribute`` system;
    only attributes flagged ``is_variant`` are rendered as selectors.
    """

    id = models.BigAutoField(primary_key=True)
    active = models.BooleanField(_("Active"), default=True)

    # Translatable admin label only (e.g. "Μαγνητικά κλιπ καλωδίων 6τμχ").
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=255, blank=True, null=True)
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Product Variant Group")
        verbose_name_plural = _("Product Variant Groups")
        ordering = ["-created_at"]
        # Explicit short index names — the model name is too long for the
        # mixin's "%(class)s_..." template (Postgres caps names at 32 chars).
        indexes = [
            BTreeIndex(fields=["created_at"], name="pvgroup_created_at_ix"),
            BTreeIndex(fields=["updated_at"], name="pvgroup_updated_at_ix"),
            BTreeIndex(fields=["active"], name="pvgroup_active_ix"),
        ]

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __repr__(self):
        name = self.safe_translation_getter("name", any_language=True) or ""
        return f"<ProductVariantGroup: {name} ({self.pk})>"
