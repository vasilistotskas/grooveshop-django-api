from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)

if TYPE_CHECKING:
    from typing import Self


class CountryQuerySet(TranslatableOptimizedQuerySet):
    """
    Optimized QuerySet for Country model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_regions(self) -> Self:
        """Prefetch regions with their translations."""
        return self.prefetch_related("regions", "regions__translations")

    def active_countries(self) -> Self:
        """Filter countries with ISO country code."""
        return self.filter(iso_cc__isnull=False)

    def by_continent(self, continent) -> Self:
        """Filter countries by continent."""
        return self

    def with_phone_code(self) -> Self:
        """Filter countries that have a phone code."""
        return self.filter(phone_code__isnull=False)

    def search_by_name(self, query) -> Self:
        """Search countries by name in translations."""
        return self.filter(translations__name__icontains=query)

    def search_by_code(self, query) -> Self:
        """Search countries by alpha-2 or alpha-3 code."""
        query_upper = query.upper()
        return self.filter(
            models.Q(alpha_2__icontains=query_upper)
            | models.Q(alpha_3__icontains=query_upper)
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes translations.
        """
        return self.with_translations()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes translations and regions.
        """
        return self.with_translations().with_regions()


class CountryManager(TranslatableOptimizedManager):
    """
    Manager for Country model.

    Methods not explicitly defined are automatically delegated to
    CountryQuerySet via __getattr__.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return Country.objects.for_list()
            return Country.objects.for_detail()
    """

    queryset_class = CountryQuerySet

    def get_queryset(self) -> CountryQuerySet:
        """Return the base queryset."""
        return CountryQuerySet(self.model, using=self._db)

    def for_list(self) -> CountryQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> CountryQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
