from __future__ import annotations

from typing import TYPE_CHECKING

from parler.managers import TranslatableManager, TranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class BlogTagQuerySet(TranslatableQuerySet):
    """
    Optimized QuerySet for BlogTag model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

    def active_only(self) -> Self:
        """Filter to active tags only."""
        return self.filter(active=True)

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes translations.
        """
        return self.with_translations()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Same as for_list() for this simple model.
        """
        return self.for_list()


class BlogTagManager(TranslatableManager):
    """
    Manager for BlogTag model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return BlogTag.objects.for_list()
            return BlogTag.objects.for_detail()
    """

    def get_queryset(self) -> BlogTagQuerySet:
        return BlogTagQuerySet(self.model, using=self._db).active_only()

    def for_list(self) -> BlogTagQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> BlogTagQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()

    def all_tags(self) -> BlogTagQuerySet:
        """Return all tags including inactive ones."""
        return BlogTagQuerySet(self.model, using=self._db)
