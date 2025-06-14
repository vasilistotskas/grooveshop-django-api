from datetime import timedelta

from django.db import models
from django.db.models import Q
from django.utils import timezone

from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationPriorityEnum,
)


class NotificationQuerySet(models.QuerySet):
    def active(self):
        return self.filter(
            Q(expiry_date__isnull=True) | Q(expiry_date__gt=timezone.now())
        )

    def expired(self):
        return self.filter(expiry_date__lt=timezone.now())

    def by_kind(self, kind):
        return self.filter(kind=kind)

    def by_category(self, category):
        return self.filter(category=category)

    def by_priority(self, priority):
        return self.filter(priority=priority)

    def high_priority(self):
        return self.filter(
            priority__in=[
                NotificationPriorityEnum.HIGH,
                NotificationPriorityEnum.URGENT,
                NotificationPriorityEnum.CRITICAL,
            ]
        )

    def recent(self, days=7):
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff_date)

    def by_date_range(self, start_date, end_date):
        return self.filter(created_at__date__range=[start_date, end_date])

    def with_translations(self):
        return self.prefetch_related("translations")

    def with_user_data(self):
        return self.prefetch_related("user__user")

    def system_notifications(self):
        return self.filter(category=NotificationCategoryEnum.SYSTEM)

    def user_notifications(self):
        return self.exclude(category=NotificationCategoryEnum.SYSTEM)

    def order_related(self):
        return self.filter(category=NotificationCategoryEnum.ORDER)

    def cart_related(self):
        return self.filter(category=NotificationCategoryEnum.CART)

    def security_related(self):
        return self.filter(category=NotificationCategoryEnum.SECURITY)

    def promotional(self):
        return self.filter(category=NotificationCategoryEnum.PROMOTION)

    def with_link(self):
        return self.exclude(link="")

    def scheduled_for_today(self):
        today = timezone.now().date()
        return self.filter(created_at__date=today)

    def needs_cleanup(self, days_old=90):
        cutoff_date = timezone.now() - timedelta(days=days_old)
        return self.filter(
            expiry_date__lt=timezone.now(),
            created_at__lt=cutoff_date,
        )


class NotificationManager(models.Manager):
    def get_queryset(self) -> NotificationQuerySet:
        return NotificationQuerySet(self.model, using=self._db)

    def active(self):
        return self.get_queryset().active()

    def expired(self):
        return self.get_queryset().expired()

    def by_kind(self, kind):
        return self.get_queryset().by_kind(kind)

    def by_category(self, category):
        return self.get_queryset().by_category(category)

    def by_priority(self, priority):
        return self.get_queryset().by_priority(priority)

    def high_priority(self):
        return self.get_queryset().high_priority()

    def recent(self, days=7):
        return self.get_queryset().recent(days)

    def by_date_range(self, start_date, end_date):
        return self.get_queryset().by_date_range(start_date, end_date)

    def with_translations(self):
        return self.get_queryset().with_translations()

    def with_user_data(self):
        return self.get_queryset().with_user_data()

    def system_notifications(self):
        return self.get_queryset().system_notifications()

    def user_notifications(self):
        return self.get_queryset().user_notifications()

    def order_related(self):
        return self.get_queryset().order_related()

    def cart_related(self):
        return self.get_queryset().cart_related()

    def security_related(self):
        return self.get_queryset().security_related()

    def promotional(self):
        return self.get_queryset().promotional()

    def with_link(self):
        return self.get_queryset().with_link()

    def scheduled_for_today(self):
        return self.get_queryset().scheduled_for_today()

    def needs_cleanup(self, days_old=90):
        return self.get_queryset().needs_cleanup(days_old)

    def cleanup_expired(self, days_old=90):
        expired_notifications = self.needs_cleanup(days_old)
        count = expired_notifications.count()
        expired_notifications.delete()
        return count

    def get_notifications_needing_attention(self):
        return self.get_queryset().filter(
            priority__in=[
                NotificationPriorityEnum.URGENT,
                NotificationPriorityEnum.CRITICAL,
            ],
            expiry_date__gt=timezone.now(),
        )

    def bulk_mark_expired(self):
        return (
            self.get_queryset()
            .filter(expiry_date__lt=timezone.now())
            .update(kind=NotificationKindEnum.WARNING)
        )


class NotificationUserQuerySet(models.QuerySet):
    def seen(self):
        return self.filter(seen=True)

    def unseen(self):
        return self.filter(seen=False)

    def for_user(self, user):
        return self.filter(user=user)

    def recent(self, days=7):
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff_date)

    def by_notification_kind(self, kind):
        return self.filter(notification__kind=kind)

    def by_notification_category(self, category):
        return self.filter(notification__category=category)

    def by_notification_priority(self, priority):
        return self.filter(notification__priority=priority)

    def high_priority(self):
        return self.filter(
            notification__priority__in=[
                NotificationPriorityEnum.HIGH,
                NotificationPriorityEnum.URGENT,
                NotificationPriorityEnum.CRITICAL,
            ]
        )

    def with_notification_data(self):
        return self.select_related("notification").prefetch_related(
            "notification__translations"
        )

    def with_user_data(self):
        return self.select_related("user")

    def optimized_for_list(self):
        return self.select_related("notification", "user").prefetch_related(
            "notification__translations"
        )

    def active_notifications(self):
        return self.filter(
            Q(notification__expiry_date__isnull=True)
            | Q(notification__expiry_date__gt=timezone.now())
        )

    def expired_notifications(self):
        return self.filter(notification__expiry_date__lt=timezone.now())

    def seen_today(self):
        today = timezone.now().date()
        return self.filter(seen=True, seen_at__date=today)

    def by_date_range(self, start_date, end_date):
        return self.filter(created_at__date__range=[start_date, end_date])

    def order_related(self):
        return self.filter(
            notification__category=NotificationCategoryEnum.ORDER
        )

    def cart_related(self):
        return self.filter(notification__category=NotificationCategoryEnum.CART)

    def security_related(self):
        return self.filter(
            notification__category=NotificationCategoryEnum.SECURITY
        )

    def promotional(self):
        return self.filter(
            notification__category=NotificationCategoryEnum.PROMOTION
        )


class NotificationUserManager(models.Manager):
    def get_queryset(self) -> NotificationUserQuerySet:
        return NotificationUserQuerySet(self.model, using=self._db)

    def seen(self):
        return self.get_queryset().seen()

    def unseen(self):
        return self.get_queryset().unseen()

    def for_user(self, user):
        return self.get_queryset().for_user(user)

    def recent(self, days=7):
        return self.get_queryset().recent(days)

    def by_notification_kind(self, kind):
        return self.get_queryset().by_notification_kind(kind)

    def by_notification_category(self, category):
        return self.get_queryset().by_notification_category(category)

    def by_notification_priority(self, priority):
        return self.get_queryset().by_notification_priority(priority)

    def high_priority(self):
        return self.get_queryset().high_priority()

    def with_notification_data(self):
        return self.get_queryset().with_notification_data()

    def with_user_data(self):
        return self.get_queryset().with_user_data()

    def optimized_for_list(self):
        return self.get_queryset().optimized_for_list()

    def active_notifications(self):
        return self.get_queryset().active_notifications()

    def expired_notifications(self):
        return self.get_queryset().expired_notifications()

    def seen_today(self):
        return self.get_queryset().seen_today()

    def by_date_range(self, start_date, end_date):
        return self.get_queryset().by_date_range(start_date, end_date)

    def order_related(self):
        return self.get_queryset().order_related()

    def cart_related(self):
        return self.get_queryset().cart_related()

    def security_related(self):
        return self.get_queryset().security_related()

    def promotional(self):
        return self.get_queryset().promotional()

    def bulk_mark_seen(self, user, notification_ids=None):
        queryset = self.for_user(user).unseen()
        if notification_ids:
            queryset = queryset.filter(notification__id__in=notification_ids)

        return queryset.update(seen=True, seen_at=timezone.now())
