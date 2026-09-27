from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Exists, OuterRef

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

    def with_has_regions(self) -> Self:
        """Annotate whether the country has any ``Region`` row.

        A single correlated ``EXISTS`` per query, not a prefetch — the
        storefront needs this on every list row to decide whether to
        show a region field at all (most of the full ISO 3166-1 seed
        has none), and a prefetch-then-``bool(regions.all())`` would
        pull every region row just to answer yes/no.
        """
        from region.models import Region

        return self.annotate(
            has_regions=Exists(Region.objects.filter(country_id=OuterRef("pk")))
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes translations and the ``has_regions`` annotation.
        """
        return self.with_translations().with_has_regions()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes translations, regions and the ``has_regions``
        annotation.
        """
        return self.with_translations().with_regions().with_has_regions()


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
