from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from typing import Self


class VatQuerySet(models.QuerySet):
    """
    Optimized QuerySet for Vat model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Simple model with no relations to prefetch.
        """
        return self

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Same as for_list() for this simple model.
        """
        return self.for_list()

    def by_value(self, value) -> Self:
        """Filter by exact VAT value."""
        return self.filter(value=value)

    def above_value(self, value) -> Self:
        """Filter VAT rates above a certain value."""
        return self.filter(value__gt=value)

    def below_value(self, value) -> Self:
        """Filter VAT rates below a certain value."""
        return self.filter(value__lt=value)


class VatManager(models.Manager):
    """
    Manager for Vat model with optimized queryset methods.

    Most methods are automatically delegated to VatQuerySet
    via __getattr__. Only for_list() and for_detail() are explicitly
    defined for IDE support.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return Vat.objects.for_list()
            return Vat.objects.for_detail()
    """

    queryset_class = VatQuerySet

    def get_queryset(self) -> VatQuerySet:
        return VatQuerySet(self.model, using=self._db)

    def for_list(self) -> VatQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> VatQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
