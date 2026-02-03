from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from product.managers.attribute_value import AttributeValueManager


class AttributeValue(
    TranslatableModel, SortableModel, TimeStampMixinModel, UUIDModel
):
    """
    Defines a specific value for an attribute (e.g., "Large", "Red", "256GB").
    Translatable value allows multi-language support.
    """

    id = models.BigAutoField(primary_key=True)
    attribute = models.ForeignKey(
        "product.Attribute",
        on_delete=models.CASCADE,
        related_name="values",
    )
    active = models.BooleanField(_("Active"), default=True)

    # Parler translations
    translations = TranslatedFields(
        value=models.CharField(_("Value"), max_length=255)
    )

    objects: AttributeValueManager = AttributeValueManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Attribute Value")
        verbose_name_plural = _("Attribute Values")
        ordering = ["attribute", "sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(
                fields=["attribute"], name="attribute_value_attribute_ix"
            ),
            BTreeIndex(fields=["active"], name="attribute_value_active_ix"),
        ]

    def __str__(self):
        value = self.safe_translation_getter("value", any_language=True) or ""
        attribute_name = (
            self.attribute.safe_translation_getter("name", any_language=True)
            if self.attribute_id
            else ""
        )
        return f"{attribute_name}: {value}"

    def __repr__(self):
        value = self.safe_translation_getter("value", any_language=True) or ""
        return f"<AttributeValue: {value}>"

    def get_ordering_queryset(self):
        """Override to scope ordering within the same attribute."""
        return AttributeValue.objects.filter(attribute=self.attribute)
