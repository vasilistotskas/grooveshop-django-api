from __future__ import annotations

from typing import TYPE_CHECKING, Self

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)

if TYPE_CHECKING:
    from loyalty.models.tier import LoyaltyTier


class LoyaltyTierQuerySet(TranslatableOptimizedQuerySet):
    def get_for_level(self, level: int) -> LoyaltyTier | None:
        """Return the highest tier where required_level <= level."""
        return (
            self.filter(required_level__lte=level)
            .order_by("-required_level")
            .first()
        )

    def get_next_tier(
        self, current_tier: LoyaltyTier | None
    ) -> LoyaltyTier | None:
        """Return the next tier above the current one."""
        if current_tier is None:
            return self.order_by("required_level").first()
        return (
            self.filter(required_level__gt=current_tier.required_level)
            .order_by("required_level")
            .first()
        )

    def for_list(self) -> Self:
        return self.with_translations()

    def for_detail(self) -> Self:
        return self.for_list()


class LoyaltyTierManager(TranslatableOptimizedManager):
    queryset_class = LoyaltyTierQuerySet
