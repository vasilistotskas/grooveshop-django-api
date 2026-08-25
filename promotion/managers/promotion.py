from __future__ import annotations

from typing import Self

from django.db.models import Q
from django.utils import timezone

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from promotion.enum import PromotionTrigger


class PromotionQuerySet(TranslatableOptimizedQuerySet):
    def live(self) -> Self:
        """Active promotions inside their schedule window."""
        now = timezone.now()
        return self.filter(is_active=True).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now),
            Q(ends_at__isnull=True) | Q(ends_at__gt=now),
        )

    def automatic(self) -> Self:
        return self.filter(trigger=PromotionTrigger.AUTOMATIC)

    def for_list(self) -> Self:
        return self.with_translations()

    def for_detail(self) -> Self:
        return self.with_translations().prefetch_related(
            "codes", "products", "categories"
        )


class PromotionManager(TranslatableOptimizedManager):
    queryset_class = PromotionQuerySet

    def live(self) -> PromotionQuerySet:
        return self.get_queryset().live()

    def automatic(self) -> PromotionQuerySet:
        return self.get_queryset().automatic()
