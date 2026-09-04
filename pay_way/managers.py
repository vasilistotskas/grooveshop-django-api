from __future__ import annotations

from typing import TYPE_CHECKING

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)

if TYPE_CHECKING:
    from typing import Self


class PayWayQuerySet(TranslatableOptimizedQuerySet):
    """
    QuerySet for the PayWay model.

    ``with_translations()`` / ``for_list()`` / ``for_detail()`` are
    inherited from ``TranslatableOptimizedQuerySet``; only the
    payment-specific filters live here.
    """

    def active(self) -> Self:
        return self.filter(active=True)

    def inactive(self) -> Self:
        return self.filter(active=False)


class PayWayManager(TranslatableOptimizedManager):
    """
    Manager for the PayWay model.

    ``for_list()`` / ``for_detail()`` and delegation of queryset methods
    (``active()``, ``inactive()``) are provided by
    ``TranslatableOptimizedManager``.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return PayWay.objects.for_list()
            return PayWay.objects.for_detail()
    """

    queryset_class = PayWayQuerySet
