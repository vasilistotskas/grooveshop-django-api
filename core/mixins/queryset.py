"""
Reusable QuerySet mixins for common optimization patterns.

This module provides mixins that can be composed into custom QuerySets
to handle common patterns like soft delete filtering.

Usage:
    class ProductQuerySet(SoftDeleteQuerySetMixin, models.QuerySet):
        def active(self):
            return self.exclude_deleted().filter(active=True)

Note:
    Mixins should be listed before models.QuerySet in the inheritance chain
    to ensure proper method resolution order (MRO).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Self


class SoftDeleteQuerySetMixin:
    """
    Mixin providing soft delete query methods.

    Models using this mixin should have `is_deleted` and `deleted_at` fields.
    This mixin provides methods to filter records based on their deletion status.

    Example:
        class ProductQuerySet(SoftDeleteQuerySetMixin, models.QuerySet):
            def active(self):
                return self.exclude_deleted().filter(active=True)
    """

    def exclude_deleted(self) -> Self:
        """
        Exclude soft-deleted records.

        Returns:
            QuerySet excluding records where is_deleted=True.
        """
        return self.exclude(is_deleted=True)

    def with_deleted(self) -> Self:
        """
        Include all records regardless of deletion status.

        Returns:
            QuerySet with all records (both deleted and non-deleted).
        """
        return self.all()

    def deleted_only(self) -> Self:
        """
        Return only soft-deleted records.

        Returns:
            QuerySet containing only records where is_deleted=True.
        """
        return self.filter(is_deleted=True)
