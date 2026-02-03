from __future__ import annotations

from typing import TYPE_CHECKING

from parler.managers import TranslatableManager, TranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class PayWayQuerySet(TranslatableQuerySet):
    """
    Optimized QuerySet for PayWay model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

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

    def active(self):
        return self.filter(active=True)

    def inactive(self):
        return self.filter(active=False)

    def online_payments(self):
        return self.filter(is_online_payment=True)

    def offline_payments(self):
        return self.filter(is_online_payment=False)


class PayWayManager(TranslatableManager):
    """
    Manager for PayWay model with optimized queryset methods.

    Most methods are automatically delegated to PayWayQuerySet
    via __getattr__. Only for_list() and for_detail() are explicitly
    defined for IDE support.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return PayWay.objects.for_list()
            return PayWay.objects.for_detail()
    """

    queryset_class = PayWayQuerySet

    def get_queryset(self) -> PayWayQuerySet:
        return PayWayQuerySet(self.model, using=self._db)

    def for_list(self) -> PayWayQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> PayWayQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
