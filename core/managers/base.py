"""
Base manager and queryset classes for optimized database queries.

These classes provide a consistent pattern for query optimization across all apps,
eliminating N+1 query problems through standardized `for_list()` and `for_detail()` methods.

Pattern:
    1. Create a QuerySet class extending OptimizedQuerySet
    2. Override `for_list()` and `for_detail()` with appropriate optimizations
    3. Create a Manager class extending OptimizedManager
    4. Set `queryset_class` to your QuerySet class
    5. Use manager methods in ViewSet's `get_queryset()`

Example:
    class ProductQuerySet(OptimizedQuerySet):
        def with_category(self):
            return self.select_related('category', 'vat')

        def with_counts(self):
            return self.annotate(
                _likes_count=Count('favourites', distinct=True),
            )

        def for_list(self):
            return self.with_translations().with_category().with_counts()

        def for_detail(self):
            return self.for_list().prefetch_related('images', 'tags')

    class ProductManager(OptimizedManager):
        queryset_class = ProductQuerySet
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models

if TYPE_CHECKING:
    from typing import Self


class OptimizedQuerySet(models.QuerySet):
    """
    Base QuerySet with common optimization patterns.

    Subclasses should override `for_list()` and `for_detail()` to provide
    optimized querysets for list and detail views respectively.
    """

    def with_translations(self) -> Self:
        """
        Prefetch translations for Parler models.

        Override this method if your model uses a different translation pattern.
        """
        if hasattr(self.model, "translations"):
            return self.prefetch_related("translations")
        return self

    def for_list(self) -> Self:
        """
        Return an optimized queryset for list views.

        Override in subclass to add:
        - select_related() for ForeignKey fields displayed in list
        - prefetch_related() for ManyToMany/reverse FK fields
        - annotate() for count/aggregate properties
        - with_translations() for translatable models
        """
        return self.with_translations()

    def for_detail(self) -> Self:
        """
        Return an optimized queryset for detail views.

        Override in subclass to add additional prefetches beyond for_list().
        Typically includes more related data than list view.
        """
        return self.for_list()


class OptimizedManager(models.Manager):
    """
    Base Manager that uses OptimizedQuerySet.

    Set `queryset_class` to your custom QuerySet class.

    Methods not explicitly defined on the Manager are automatically
    delegated to the QuerySet via __getattr__.

    Example:
        class ProductManager(OptimizedManager):
            queryset_class = ProductQuerySet
    """

    queryset_class: type[OptimizedQuerySet] = OptimizedQuerySet

    def get_queryset(self) -> OptimizedQuerySet:
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

    def for_list(self) -> OptimizedQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> OptimizedQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()


# Parler-compatible versions for translatable models
try:
    from parler.managers import TranslatableManager, TranslatableQuerySet

    class TranslatableOptimizedQuerySet(TranslatableQuerySet):
        """
        Base QuerySet for Parler translatable models with optimization patterns.
        """

        def with_translations(self) -> Self:
            """Prefetch translations for better performance."""
            return self.prefetch_related("translations")

        def for_list(self) -> Self:
            """Return optimized queryset for list views."""
            return self.with_translations()

        def for_detail(self) -> Self:
            """Return optimized queryset for detail views."""
            return self.for_list()

    class TranslatableOptimizedManager(TranslatableManager):
        """
        Base Manager for Parler translatable models.

        Set `queryset_class` to your custom QuerySet class.

        Methods not explicitly defined on the Manager are automatically
        delegated to the QuerySet via __getattr__.
        """

        queryset_class: type[TranslatableOptimizedQuerySet] = (
            TranslatableOptimizedQuerySet
        )

        def get_queryset(self) -> TranslatableOptimizedQuerySet:
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

        def for_list(self) -> TranslatableOptimizedQuerySet:
            """Return optimized queryset for list views."""
            return self.get_queryset().for_list()

        def for_detail(self) -> TranslatableOptimizedQuerySet:
            """Return optimized queryset for detail views."""
            return self.get_queryset().for_detail()

except ImportError:
    # Parler not installed, provide stub classes
    TranslatableOptimizedQuerySet = OptimizedQuerySet  # type: ignore[misc]
    TranslatableOptimizedManager = OptimizedManager  # type: ignore[misc]
