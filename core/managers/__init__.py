"""
Core manager classes for optimized querysets.

This module provides base classes for creating optimized managers and querysets
that follow consistent patterns across all Django apps.

Usage:
    from core.managers import OptimizedQuerySet, OptimizedManager

    class ProductQuerySet(OptimizedQuerySet):
        def for_list(self):
            return self.with_translations().select_related('category')

        def for_detail(self):
            return self.for_list().prefetch_related('images', 'tags')

    class ProductManager(OptimizedManager):
        queryset_class = ProductQuerySet
"""

from core.managers.base import (
    OptimizedManager,
    OptimizedQuerySet,
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from core.managers.tree import (
    TreeTranslatableManager,
    TreeTranslatableQuerySet,
)

__all__ = [
    "OptimizedQuerySet",
    "OptimizedManager",
    "TranslatableOptimizedQuerySet",
    "TranslatableOptimizedManager",
    "TreeTranslatableQuerySet",
    "TreeTranslatableManager",
]
