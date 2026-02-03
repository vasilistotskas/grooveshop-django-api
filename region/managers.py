from __future__ import annotations

from typing import TYPE_CHECKING

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)

if TYPE_CHECKING:
    from typing import Self


class RegionQuerySet(TranslatableOptimizedQuerySet):
    """
    Optimized QuerySet for Region model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_country(self) -> Self:
        """Select related country with translations."""
        return self.select_related("country").prefetch_related(
            "country__translations"
        )

    def by_country(self, country_code) -> Self:
        return self.filter(country__alpha_2=country_code.upper())

    def by_country_name(self, country_name) -> Self:
        return self.filter(country__translations__name__icontains=country_name)

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes translations and country.
        """
        return self.with_translations().with_country()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Same as for_list() for regions.
        """
        return self.for_list()


class RegionManager(TranslatableOptimizedManager):
    """
    Manager for Region model with optimized queryset methods.

    Methods not explicitly defined on the Manager are automatically
    delegated to the QuerySet via __getattr__.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return Region.objects.for_list()
            return Region.objects.for_detail()
    """

    queryset_class = RegionQuerySet

    def get_queryset(self) -> RegionQuerySet:
        return RegionQuerySet(self.model, using=self._db)

    def for_list(self) -> RegionQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> RegionQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
