from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from product.enum.attribute import SchemaProperty
from product.managers.attribute import AttributeManager


class Attribute(
    TranslatableModel, SortableModel, TimeStampMixinModel, UUIDModel
):
    """
    Defines an attribute type (e.g., Size, Color, Capacity).
    Translatable name allows multi-language support.
    """

    id = models.BigAutoField(primary_key=True)
    active = models.BooleanField(_("Active"), default=True)
    is_variant = models.BooleanField(
        _("Is Variant Axis"),
        default=False,
        help_text=_(
            "When enabled, this attribute is rendered as a variant selector "
            "(e.g. Colour, Memory) across a product's variant group instead of "
            "as a plain read-only specification."
        ),
    )

    schema_property = models.CharField(
        _("Standard property"),
        max_length=20,
        choices=SchemaProperty.choices,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "Which standard product property this attribute is (for "
            "example Colour or Size). Search engines use it to understand "
            "and group your products. Leave empty if it is none of these."
        ),
    )

    # Parler translations
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=100)
    )

    objects: AttributeManager = AttributeManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Attribute")
        verbose_name_plural = _("Attributes")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(fields=["active"], name="attribute_active_ix"),
            BTreeIndex(fields=["is_variant"], name="attribute_is_variant_ix"),
        ]

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __repr__(self):
        name = self.safe_translation_getter("name", any_language=True) or ""
        return f"<Attribute: {name}>"
