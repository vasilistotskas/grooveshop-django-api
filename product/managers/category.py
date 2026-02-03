from __future__ import annotations

from typing import TYPE_CHECKING

from core.managers import TreeTranslatableManager, TreeTranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class CategoryQuerySet(TreeTranslatableQuerySet):
    """
    Optimized QuerySet for ProductCategory model.

    Combines Parler translations with MPTT tree structure.
    """

    def active(self) -> Self:
        return self.filter(active=True)

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


class CategoryManager(TreeTranslatableManager):
    """
    Manager for ProductCategory model with optimized queryset methods.
    """

    queryset_class = CategoryQuerySet

    def get_queryset(self) -> CategoryQuerySet:
        return self.queryset_class(self.model, using=self._db)

    def for_list(self) -> CategoryQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> CategoryQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
