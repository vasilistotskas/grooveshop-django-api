from django.db import models
from parler.managers import TranslatableManager, TranslatableQuerySet


class SubscriptionTopicQuerySet(TranslatableQuerySet):
    """
    Optimized QuerySet for SubscriptionTopic model.

    Provides `for_list()` and `for_detail()` methods for consistent
    query optimization across ViewSets.
    """

    def active(self):
        """Filter only active topics."""
        return self.filter(is_active=True)

    def by_category(self, category):
        """Filter by category."""
        return self.filter(category=category)

    def default_topics(self):
        """Get topics that are default subscriptions."""
        return self.filter(is_default=True, is_active=True)

    def for_list(self):
        """
        Optimized queryset for list views.
        Includes translations and subscriber counts.
        """
        return self.prefetch_related("translations")

    def for_detail(self):
        """
        Optimized queryset for detail views.
        Includes all related data.
        """
        return self.for_list()


class SubscriptionTopicManager(TranslatableManager):
    """
    Manager for SubscriptionTopic model with optimized queryset methods.
    """

    def get_queryset(self) -> SubscriptionTopicQuerySet:
        return SubscriptionTopicQuerySet(self.model, using=self._db)

    def for_list(self):
        """Get optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self):
        """Get optimized queryset for detail views."""
        return self.get_queryset().for_detail()

    def active(self):
        """Get only active topics."""
        return self.get_queryset().active()

    def default_topics(self):
        """Get default subscription topics."""
        return self.get_queryset().default_topics()


class UserSubscriptionQuerySet(models.QuerySet):
    """
    Optimized QuerySet for UserSubscription model.

    Provides `for_list()` and `for_detail()` methods for consistent
    query optimization across ViewSets.
    """

    def active(self):
        """Filter only active subscriptions."""
        return self.filter(status="ACTIVE")

    def pending(self):
        """Filter pending subscriptions."""
        return self.filter(status="PENDING")

    def for_user(self, user):
        """Filter subscriptions for a specific user."""
        return self.filter(user=user)

    def for_topic(self, topic):
        """Filter subscriptions for a specific topic."""
        return self.filter(topic=topic)

    def for_list(self):
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

    def for_detail(self):
        """
        Optimized queryset for detail views.
        Includes all related data.
        """
        return self.for_list()


class UserSubscriptionManager(models.Manager):
    """
    Manager for UserSubscription model with optimized queryset methods.
    """

    def get_queryset(self) -> UserSubscriptionQuerySet:
        return UserSubscriptionQuerySet(self.model, using=self._db)

    def for_list(self):
        """Get optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self):
        """Get optimized queryset for detail views."""
        return self.get_queryset().for_detail()

    def active(self):
        """Get only active subscriptions."""
        return self.get_queryset().active()

    def for_user(self, user):
        """Get subscriptions for a specific user."""
        return self.get_queryset().for_user(user)
