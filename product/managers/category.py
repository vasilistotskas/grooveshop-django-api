from __future__ import annotations

from typing import TYPE_CHECKING

from mptt.managers import TreeManager
from mptt.querysets import TreeQuerySet
from parler.managers import TranslatableManager, TranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class CategoryQuerySet(TranslatableQuerySet, TreeQuerySet):
    """
    Optimized QuerySet for ProductCategory model.

    Combines Parler translations with MPTT tree structure.
    """

    @classmethod
    def as_manager(cls):
        manager = CategoryManager.from_queryset(cls)()
        manager._built_with_as_manager = True
        return manager

    as_manager.queryset_only = True  # type: ignore[attr-defined]

    def active(self) -> Self:
        return self.filter(active=True)

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

    def with_parent(self) -> Self:
        """Select related parent category."""
        return self.select_related("parent")

    def with_products_count(self) -> Self:
        """Annotate with products count."""
        from django.db.models import Count

        return self.annotate(_products_count=Count("products", distinct=True))

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes translations and parent.
        """
        return self.with_translations().with_parent()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus products count.
        """
        return self.for_list().with_products_count()


class CategoryManager(TreeManager, TranslatableManager):
    """
    Manager for ProductCategory model with optimized queryset methods.
    """

    _queryset_class = CategoryQuerySet

    def get_queryset(self) -> CategoryQuerySet:
        return self._queryset_class(self.model, using=self._db)

    def for_list(self) -> CategoryQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> CategoryQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()

    def active(self) -> CategoryQuerySet:
        return self.get_queryset().active()
