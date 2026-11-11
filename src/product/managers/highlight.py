from __future__ import annotations

from typing import TYPE_CHECKING

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)

if TYPE_CHECKING:
    from typing import Self


class ProductHighlightQuerySet(TranslatableOptimizedQuerySet):
    """Optimized QuerySet for ProductHighlight."""

    def in_display_order(self) -> Self:
        """The merchant's order; ``id`` breaks a tie deterministically."""
        return self.order_by("sort_order", "id")

    def for_list(self) -> Self:
        """Every translation in one query, in the merchant's order."""
        return self.with_translations().in_display_order()


class ProductHighlightManager(TranslatableOptimizedManager):
    queryset_class = ProductHighlightQuerySet

    def get_queryset(self) -> ProductHighlightQuerySet:
        return ProductHighlightQuerySet(self.model, using=self._db)

    def for_list(self) -> ProductHighlightQuerySet:
        return self.get_queryset().for_list()
