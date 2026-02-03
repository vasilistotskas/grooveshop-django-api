from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from product.managers.product_attribute import ProductAttributeManager


class ProductAttribute(TimeStampMixinModel):
    """
    Many-to-many junction table linking products to attribute values.
    Tracks when attributes are assigned to products.
    """

    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        "product.Product",
        on_delete=models.CASCADE,
        related_name="product_attributes",
    )
    attribute_value = models.ForeignKey(
        "product.AttributeValue",
        on_delete=models.CASCADE,
        related_name="product_attributes",
    )

    objects: ProductAttributeManager = ProductAttributeManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Product Attribute")
        verbose_name_plural = _("Product Attributes")
        unique_together = [["product", "attribute_value"]]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["product"], name="product_attribute_product_ix"),
            BTreeIndex(
                fields=["attribute_value"], name="product_attribute_value_ix"
            ),
            BTreeIndex(
                fields=["product", "attribute_value"],
                name="product_attribute_composite_ix",
            ),
        ]

    def __str__(self):
        product_name = (
            self.product.safe_translation_getter("name", any_language=True)
            if self.product_id
            else ""
        )
        attribute_value = (
            self.attribute_value.safe_translation_getter(
                "value", any_language=True
            )
            if self.attribute_value_id
            else ""
        )
        return f"{product_name} - {attribute_value}"

    def __repr__(self):
        return f"<ProductAttribute: Product {self.product_id} - AttributeValue {self.attribute_value_id}>"
