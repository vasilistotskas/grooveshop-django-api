from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationPriorityEnum,
)

if TYPE_CHECKING:
    from typing import Any, Self


class NotificationQuerySet(TranslatableOptimizedQuerySet):
    """
    Optimized QuerySet for Notification model.
    """

    def active(self) -> Self:
        return self.filter(
            Q(expiry_date__isnull=True) | Q(expiry_date__gt=timezone.now())
        )

    def expired(self) -> Self:
        return self.filter(expiry_date__lt=timezone.now())

    def by_kind(self, kind: str) -> Self:
        return self.filter(kind=kind)

    def by_category(self, category: str) -> Self:
        return self.filter(category=category)

    def by_priority(self, priority: str) -> Self:
        return self.filter(priority=priority)

    def high_priority(self) -> Self:
        return self.filter(
            priority__in=[
                NotificationPriorityEnum.HIGH,
                NotificationPriorityEnum.URGENT,
                NotificationPriorityEnum.CRITICAL,
            ]
        )

    def recent(self, days: int = 7) -> Self:
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff_date)

    def by_date_range(self, start_date: Any, end_date: Any) -> Self:
        return self.filter(created_at__date__range=[start_date, end_date])

    def with_translations(self) -> Self:
        return self.prefetch_related("translations")

    def with_user_data(self) -> Self:
        return self.prefetch_related("user__user")

    def for_list(self) -> Self:
        """Optimized queryset for list views."""
        return self.with_translations()

    def for_detail(self) -> Self:
        """Optimized queryset for detail views."""
        return self.with_translations().with_user_data()

    def system_notifications(self) -> Self:
        return self.filter(category=NotificationCategoryEnum.SYSTEM)

    def user_notifications(self) -> Self:
        return self.exclude(category=NotificationCategoryEnum.SYSTEM)

    def order_related(self) -> Self:
        return self.filter(category=NotificationCategoryEnum.ORDER)

    def cart_related(self) -> Self:
        return self.filter(category=NotificationCategoryEnum.CART)

    def security_related(self) -> Self:
        return self.filter(category=NotificationCategoryEnum.SECURITY)

    def promotional(self) -> Self:
        return self.filter(category=NotificationCategoryEnum.PROMOTION)

    def with_link(self) -> Self:
        return self.exclude(link="")

    def scheduled_for_today(self) -> Self:
        today = timezone.now().date()
        return self.filter(created_at__date=today)

    def needs_cleanup(self, days_old: int = 90) -> Self:
        cutoff_date = timezone.now() - timedelta(days=days_old)
        return self.filter(
            expiry_date__lt=timezone.now(),
            created_at__lt=cutoff_date,
        )


class NotificationManager(TranslatableOptimizedManager):
    """
    Manager for Notification model.

    Most methods are automatically delegated to NotificationQuerySet
    via __getattr__. Only for_list(), for_detail(), and business logic
    methods are explicitly defined.
    """

    queryset_class = NotificationQuerySet

    def get_queryset(self) -> NotificationQuerySet:
        return NotificationQuerySet(self.model, using=self._db)

    def for_list(self) -> NotificationQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> NotificationQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()

    def cleanup_expired(self, days_old: int = 90) -> int:
        """Delete expired notifications older than specified days."""
        expired_notifications = self.get_queryset().needs_cleanup(days_old)
        count = expired_notifications.count()
        expired_notifications.delete()
        return count

    def get_notifications_needing_attention(self) -> NotificationQuerySet:
        """Get urgent and critical notifications that haven't expired."""
        return self.get_queryset().filter(
            priority__in=[
                NotificationPriorityEnum.URGENT,
                NotificationPriorityEnum.CRITICAL,
            ],
            expiry_date__gt=timezone.now(),
        )

    def bulk_mark_expired(self) -> int:
        """Mark all expired notifications as warnings."""
        return (
            self.get_queryset()
            .filter(expiry_date__lt=timezone.now())
            .update(kind=NotificationKindEnum.WARNING)
        )


class NotificationUserQuerySet(models.QuerySet):
    """
    Optimized QuerySet for NotificationUser model.
    """

    def seen(self) -> Self:
        return self.filter(seen=True)

    def unseen(self) -> Self:
        return self.filter(seen=False)

    def for_user(self, user: Any) -> Self:
        return self.filter(user=user)

    def recent(self, days: int = 7) -> Self:
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff_date)

    def by_notification_kind(self, kind: str) -> Self:
        return self.filter(notification__kind=kind)

    def by_notification_category(self, category: str) -> Self:
        return self.filter(notification__category=category)

    def by_notification_priority(self, priority: str) -> Self:
        return self.filter(notification__priority=priority)

    def high_priority(self) -> Self:
        return self.filter(
            notification__priority__in=[
                NotificationPriorityEnum.HIGH,
                NotificationPriorityEnum.URGENT,
                NotificationPriorityEnum.CRITICAL,
            ]
        )

    def with_notification_data(self) -> Self:
        return self.select_related("notification").prefetch_related(
            "notification__translations"
        )

    def with_user_data(self) -> Self:
        return self.select_related("user")

    def for_list(self) -> Self:
        """Optimized queryset for list views."""
        return self.select_related("notification", "user").prefetch_related(
            "notification__translations"
        )

    def for_detail(self) -> Self:
        """Optimized queryset for detail views."""
        return self.for_list()

    def active_notifications(self) -> Self:
        return self.filter(
            Q(notification__expiry_date__isnull=True)
            | Q(notification__expiry_date__gt=timezone.now())
        )

    def expired_notifications(self) -> Self:
        return self.filter(notification__expiry_date__lt=timezone.now())

    def seen_today(self) -> Self:
        now = timezone.now()
        local_now = timezone.localtime(now)
        today = local_now.date()

        start_of_day = timezone.make_aware(
            datetime.combine(today, datetime.min.time())
        )
        end_of_day = timezone.make_aware(
            datetime.combine(today, datetime.max.time())
        )
        return self.filter(seen=True, seen_at__range=[start_of_day, end_of_day])

    def by_date_range(self, start_date: Any, end_date: Any) -> Self:
        return self.filter(created_at__date__range=[start_date, end_date])

    def order_related(self) -> Self:
        return self.filter(
            notification__category=NotificationCategoryEnum.ORDER
        )

    def cart_related(self) -> Self:
        return self.filter(notification__category=NotificationCategoryEnum.CART)

    def security_related(self) -> Self:
        return self.filter(
            notification__category=NotificationCategoryEnum.SECURITY
        )

    def promotional(self) -> Self:
        return self.filter(
            notification__category=NotificationCategoryEnum.PROMOTION
        )


class NotificationUserManager(models.Manager):
    """
    Manager for NotificationUser model.

    Most methods are automatically delegated to NotificationUserQuerySet
    via __getattr__. Only for_list(), for_detail(), and business logic
    methods are explicitly defined.
    """

    queryset_class = NotificationUserQuerySet

    def get_queryset(self) -> NotificationUserQuerySet:
        return NotificationUserQuerySet(self.model, using=self._db)

    def __getattr__(self, name: str) -> Any:
        """
        Delegate unknown attributes to the queryset.

        Methods starting with underscore raise AttributeError to prevent
        access to private/protected attributes.
        """
        if name.startswith("_"):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )
        return getattr(self.get_queryset(), name)

    def for_list(self) -> NotificationUserQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> NotificationUserQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()

    def bulk_mark_seen(
        self, user: Any, notification_ids: list[int] | None = None
    ) -> int:
        """Mark notifications as seen for a user."""
        queryset = self.get_queryset().for_user(user).unseen()
        if notification_ids:
            queryset = queryset.filter(notification__id__in=notification_ids)

        return queryset.update(seen=True, seen_at=timezone.now())
