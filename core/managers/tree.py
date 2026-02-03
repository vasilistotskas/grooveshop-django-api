"""
Tree + Translatable manager and queryset classes for MPTT models with Parler.

These classes combine django-mptt's tree operations with django-parler's
translation support, providing optimized queries for hierarchical translatable models.

Used for models like ProductCategory and BlogCategory that need both:
- Tree/hierarchical structure (MPTT)
- Multi-language translations (Parler)

Pattern:
    1. Create a QuerySet class extending TreeTranslatableQuerySet
    2. Override `for_list()` and `for_detail()` with appropriate optimizations
    3. Create a Manager class extending TreeTranslatableManager
    4. Set `queryset_class` to your QuerySet class

Example:
    class CategoryQuerySet(TreeTranslatableQuerySet):
        def active(self):
            return self.filter(active=True)

        def for_list(self):
            return self.active().with_translations().with_parent()

        def for_detail(self):
            return self.for_list().prefetch_related('children')

    class CategoryManager(TreeTranslatableManager):
        queryset_class = CategoryQuerySet
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import Self

try:
    from mptt.managers import TreeManager
    from mptt.querysets import TreeQuerySet
    from parler.managers import TranslatableManager, TranslatableQuerySet

    class TreeTranslatableQuerySet(TranslatableQuerySet, TreeQuerySet):
        """
        QuerySet combining MPTT tree operations with Parler translations.

        Used for hierarchical models like ProductCategory and BlogCategory
        that need both tree structure and multi-language support.

        Provides:
        - All MPTT tree methods (get_descendants, get_ancestors, etc.)
        - All Parler translation methods (translated, language, etc.)
        - Optimized prefetch methods for common patterns
        """

        @classmethod
        def as_manager(cls):
            """
            Return a Manager instance created from this QuerySet.

            This is required for proper integration with MPTT's TreeManager.
            """
            manager = TreeTranslatableManager.from_queryset(cls)()
            manager._built_with_as_manager = True
            return manager

        as_manager.queryset_only = True

        def with_translations(self) -> Self:
            """Prefetch translations for better performance."""
            return self.prefetch_related("translations")

        def with_parent(self) -> Self:
            """Select related parent for tree navigation."""
            return self.select_related("parent")

        def for_list(self) -> Self:
            """
            Return optimized queryset for list views.

            Override in subclass to add additional optimizations.
            Default includes translations and parent relationship.
            """
            return self.with_translations().with_parent()

        def for_detail(self) -> Self:
            """
            Return optimized queryset for detail views.

            Override in subclass to add additional prefetches beyond for_list().
            """
            return self.for_list()

    class TreeTranslatableManager(TreeManager, TranslatableManager):
        """
        Manager combining MPTT tree operations with Parler translations.

        Set `queryset_class` to your custom QuerySet class.

        Methods not explicitly defined on the Manager are automatically
        delegated to the QuerySet via __getattr__.

        Provides:
        - All MPTT tree manager methods (root_nodes, etc.)
        - All Parler translation manager methods
        - Automatic delegation to custom QuerySet methods

        Example:
            class CategoryManager(TreeTranslatableManager):
                queryset_class = CategoryQuerySet
        """

        queryset_class: type[TreeTranslatableQuerySet] = (
            TreeTranslatableQuerySet
        )
        _queryset_class: type[TreeTranslatableQuerySet] = (
            TreeTranslatableQuerySet
        )

        def __init_subclass__(cls, **kwargs):
            """Sync queryset_class with _queryset_class when subclass is created."""
            super().__init_subclass__(**kwargs)
            if hasattr(cls, "queryset_class"):
                cls._queryset_class = cls.queryset_class

        def get_queryset(self) -> TreeTranslatableQuerySet:
            """Return the base queryset."""
            return self.queryset_class(self.model, using=self._db)

        def __getattr__(self, name: str) -> Any:
            """
            Delegate unknown attributes to the queryset.

            Methods starting with underscore raise AttributeError to prevent
            access to private/protected attributes.
            """
            if name.startswith("_"):
                raise AttributeError(
                    f"'{type(self).__name__}' object has no attribute '{name}'"
                )
            return getattr(self.get_queryset(), name)

        def for_list(self) -> TreeTranslatableQuerySet:
            """Return optimized queryset for list views."""
            return self.get_queryset().for_list()

        def for_detail(self) -> TreeTranslatableQuerySet:
            """Return optimized queryset for detail views."""
            return self.get_queryset().for_detail()

except ImportError:
    # MPTT or Parler not installed, provide stub classes
    from core.managers.base import (
        TranslatableOptimizedManager,
        TranslatableOptimizedQuerySet,
    )

    TreeTranslatableQuerySet = TranslatableOptimizedQuerySet  # type: ignore[misc]
    TreeTranslatableManager = TranslatableOptimizedManager  # type: ignore[misc]
