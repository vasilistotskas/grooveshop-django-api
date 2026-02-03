from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Count
from parler.managers import TranslatableManager, TranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class AttributeValueQuerySet(TranslatableQuerySet):
    """Custom QuerySet for AttributeValue model with chainable methods."""

    def active(self) -> Self:
        """Return only active attribute values."""
        return self.filter(active=True)

    def for_attribute(self, attribute_id) -> Self:
        """Return values for a specific attribute."""
        return self.filter(attribute_id=attribute_id)

    def with_usage_count(self) -> Self:
        """Annotate with count of products using this value."""
        return self.annotate(
            usage_count=Count("product_attributes", distinct=True)
        )


class AttributeValueManager(
    TranslatableManager.from_queryset(AttributeValueQuerySet)
):
    """Custom manager for AttributeValue model with optimized queries."""

    pass
