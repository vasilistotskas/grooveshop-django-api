"""
Core mixins for reusable QuerySet optimization patterns.

This module provides mixins that can be composed into custom QuerySets
to handle common patterns like soft delete filtering.

Usage:
    from core.mixins import SoftDeleteQuerySetMixin

    class ProductQuerySet(SoftDeleteQuerySetMixin, models.QuerySet):
        def active(self):
            return self.exclude_deleted().filter(active=True)

Note:
    Mixins should be listed before models.QuerySet in the inheritance chain
    to ensure proper method resolution order (MRO).

Available Mixins:
    - SoftDeleteQuerySetMixin: For soft delete filtering operations
"""

from core.mixins.queryset import (
    SoftDeleteQuerySetMixin,
)

__all__ = [
    "SoftDeleteQuerySetMixin",
]
