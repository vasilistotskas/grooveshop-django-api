from __future__ import annotations

from typing import TYPE_CHECKING

from core.managers import OptimizedManager, OptimizedQuerySet

if TYPE_CHECKING:
    from typing import Self


class SearchQueryQuerySet(OptimizedQuerySet):
    """QuerySet for SearchQuery model."""

    def for_list(self) -> Self:
        """Optimized queryset for list views."""
        return self.select_related("user")

    def for_detail(self) -> Self:
        """Optimized queryset for detail views."""
        return self.for_list().prefetch_related("clicks")


class SearchQueryManager(OptimizedManager):
    """Manager for SearchQuery model."""

    queryset_class = SearchQueryQuerySet

    def get_queryset(self) -> SearchQueryQuerySet:
        return SearchQueryQuerySet(self.model, using=self._db)

    def for_list(self) -> SearchQueryQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> SearchQueryQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()


class SearchClickQuerySet(OptimizedQuerySet):
    """QuerySet for SearchClick model."""

    def for_list(self) -> Self:
        """Optimized queryset for list views."""
        return self.select_related("search_query")

    def for_detail(self) -> Self:
        """Optimized queryset for detail views."""
        return self.for_list()


class SearchClickManager(OptimizedManager):
    """Manager for SearchClick model."""

    queryset_class = SearchClickQuerySet

    def get_queryset(self) -> SearchClickQuerySet:
        return SearchClickQuerySet(self.model, using=self._db)

    def for_list(self) -> SearchClickQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> SearchClickQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
