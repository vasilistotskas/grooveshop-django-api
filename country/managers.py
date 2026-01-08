from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models
from parler.managers import TranslatableManager, TranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class CountryQuerySet(TranslatableQuerySet):
    """
    Optimized QuerySet for Country model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

    def with_regions(self) -> Self:
        return self.prefetch_related("regions", "regions__translations")

    def active_countries(self) -> Self:
        return self.filter(iso_cc__isnull=False)

    def by_continent(self, continent) -> Self:
        return self

    def with_phone_code(self) -> Self:
        return self.filter(phone_code__isnull=False)

    def search_by_name(self, query) -> Self:
        return self.filter(translations__name__icontains=query)

    def search_by_code(self, query) -> Self:
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


class CountryManager(TranslatableManager):
    """
    Manager for Country model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return Country.objects.for_list()
            return Country.objects.for_detail()
    """

    def get_queryset(self) -> CountryQuerySet:
        return CountryQuerySet(self.model, using=self._db)

    def for_list(self) -> CountryQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> CountryQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()

    def with_regions(self) -> CountryQuerySet:
        return self.get_queryset().with_regions()

    def active_countries(self) -> CountryQuerySet:
        return self.get_queryset().active_countries()

    def by_continent(self, continent) -> CountryQuerySet:
        return self.get_queryset().by_continent(continent)

    def with_phone_code(self) -> CountryQuerySet:
        return self.get_queryset().with_phone_code()

    def search_by_name(self, query) -> CountryQuerySet:
        return self.get_queryset().search_by_name(query)

    def search_by_code(self, query) -> CountryQuerySet:
        return self.get_queryset().search_by_code(query)
