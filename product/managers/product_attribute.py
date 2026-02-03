from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from typing import Self


class ProductAttributeQuerySet(models.QuerySet):
    """Custom QuerySet for ProductAttribute model with chainable methods."""

    def for_product(self, product_id) -> Self:
        """Return all attributes for a product with optimized queries."""
        return (
            self.filter(product_id=product_id)
            .select_related("attribute_value__attribute")
            .prefetch_related(
                "attribute_value__translations",
                "attribute_value__attribute__translations",
            )
        )

    def for_products(self, product_ids) -> Self:
        """Return all attributes for multiple products (bulk optimization)."""
        return (
            self.filter(product_id__in=product_ids)
            .select_related("attribute_value__attribute")
            .prefetch_related(
                "attribute_value__translations",
                "attribute_value__attribute__translations",
            )
        )

    def by_attribute(self, attribute_id) -> Self:
        """Return all product-attribute assignments for a specific attribute."""
        return self.filter(
            attribute_value__attribute_id=attribute_id
        ).select_related("product", "attribute_value")


class ProductAttributeManager(
    models.Manager.from_queryset(ProductAttributeQuerySet)
):
    """Custom manager for ProductAttribute model with optimized queries."""

    pass
