from __future__ import annotations

from typing import TYPE_CHECKING

from core.managers import (
    OptimizedManager,
    OptimizedQuerySet,
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)

if TYPE_CHECKING:
    from typing import Self


class SubscriptionTopicQuerySet(TranslatableOptimizedQuerySet):
    """
    Optimized QuerySet for SubscriptionTopic model.

    Provides `for_list()` and `for_detail()` methods for consistent
    query optimization across ViewSets.
    """

    def active(self) -> Self:
        """Filter only active topics."""
        return self.filter(is_active=True)

    def by_category(self, category) -> Self:
        """Filter by category."""
        return self.filter(category=category)

    def default_topics(self) -> Self:
        """Get topics that are default subscriptions."""
        return self.filter(is_default=True, is_active=True)

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.
        Includes translations and subscriber counts.
        """
        return self.with_translations()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.
        Includes all related data.
        """
        return self.for_list()


class SubscriptionTopicManager(TranslatableOptimizedManager):
    """
    Manager for SubscriptionTopic model with optimized queryset methods.

    Methods not explicitly defined are automatically delegated to
    SubscriptionTopicQuerySet via __getattr__.
    """

    queryset_class = SubscriptionTopicQuerySet

    def get_queryset(self) -> SubscriptionTopicQuerySet:
        return SubscriptionTopicQuerySet(self.model, using=self._db)

    def for_list(self) -> SubscriptionTopicQuerySet:
        """Get optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> SubscriptionTopicQuerySet:
        """Get optimized queryset for detail views."""
        return self.get_queryset().for_detail()


class UserSubscriptionQuerySet(OptimizedQuerySet):
    """
    Optimized QuerySet for UserSubscription model.

    Provides `for_list()` and `for_detail()` methods for consistent
    query optimization across ViewSets.
    """

    def active(self) -> Self:
        """Filter only active subscriptions."""
        return self.filter(status="ACTIVE")

    def pending(self) -> Self:
        """Filter pending subscriptions."""
        return self.filter(status="PENDING")

    def for_user(self, user) -> Self:
        """Filter subscriptions for a specific user."""
        return self.filter(user=user)

    def for_topic(self, topic) -> Self:
        """Filter subscriptions for a specific topic."""
        return self.filter(topic=topic)

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.
        Includes user and topic with translations.
        """
        return self.select_related(
            "user",
            "topic",
        ).prefetch_related(
            "topic__translations",
        )

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.
        Includes all related data.
        """
        return self.for_list()


class UserSubscriptionManager(OptimizedManager):
    """
    Manager for UserSubscription model with optimized queryset methods.

    Methods not explicitly defined are automatically delegated to
    UserSubscriptionQuerySet via __getattr__.
    """

    queryset_class = UserSubscriptionQuerySet

    def get_queryset(self) -> UserSubscriptionQuerySet:
        return UserSubscriptionQuerySet(self.model, using=self._db)

    def for_list(self) -> UserSubscriptionQuerySet:
        """Get optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> UserSubscriptionQuerySet:
        """Get optimized queryset for detail views."""
        return self.get_queryset().for_detail()
