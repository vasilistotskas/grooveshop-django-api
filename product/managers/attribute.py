from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Count
from parler.managers import TranslatableManager, TranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class AttributeQuerySet(TranslatableQuerySet):
    """Custom QuerySet for Attribute model with chainable methods."""

    def active(self) -> Self:
        """Return only active attributes."""
        return self.filter(active=True)

    def with_values_count(self) -> Self:
        """Annotate with count of attribute values."""
        return self.annotate(values_count=Count("values", distinct=True))

    def with_usage_count(self) -> Self:
        """Annotate with count of products using this attribute."""
        return self.annotate(
            usage_count=Count(
                "values__product_attributes__product", distinct=True
            )
        )


class AttributeManager(TranslatableManager.from_queryset(AttributeQuerySet)):
    """Custom manager for Attribute model with optimized queries."""

    pass
