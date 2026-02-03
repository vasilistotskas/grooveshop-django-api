from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.utils import timezone

from core.managers import OptimizedManager, OptimizedQuerySet

if TYPE_CHECKING:
    from typing import Self


class SearchQueryQuerySet(OptimizedQuerySet):
    """QuerySet for SearchQuery model."""

    def by_content_type(self, content_type: str) -> Self:
        """Filter by content type."""
        return self.filter(content_type=content_type)

    def with_results(self) -> Self:
        """Filter queries that returned results."""
        return self.filter(results_count__gt=0)

    def zero_results(self) -> Self:
        """Filter queries that returned no results."""
        return self.filter(results_count=0)

    def recent(self, days: int = 7) -> Self:
        """Filter queries from the last N days."""
        cutoff = timezone.now() - timedelta(days=days)
        return self.filter(timestamp__gte=cutoff)

    def for_user(self, user) -> Self:
        """Filter queries by user."""
        return self.filter(user=user)

    def with_clicks(self) -> Self:
        """Prefetch related clicks."""
        return self.prefetch_related("clicks")

    def for_list(self) -> Self:
        """Optimized queryset for list views."""
        return self.select_related("user")

    def for_detail(self) -> Self:
        """Optimized queryset for detail views."""
        return self.for_list().with_clicks()


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

    def by_result_type(self, result_type: str) -> Self:
        """Filter by result type."""
        return self.filter(result_type=result_type)

    def recent(self, days: int = 7) -> Self:
        """Filter clicks from the last N days."""
        cutoff = timezone.now() - timedelta(days=days)
        return self.filter(timestamp__gte=cutoff)

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
