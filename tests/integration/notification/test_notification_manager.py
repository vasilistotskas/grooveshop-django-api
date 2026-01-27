from datetime import timedelta

import pytest
from django.test import TestCase
from django.utils import timezone

from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationPriorityEnum,
)
from notification.factories.notification import NotificationFactory
from notification.managers import NotificationQuerySet
from notification.models.notification import Notification


@pytest.mark.django_db
class TestNotificationQuerySet(TestCase):
    def setUp(self):
        self.now = timezone.now()

        self.active_notification_1 = NotificationFactory(
            kind=NotificationKindEnum.INFO,
            category=NotificationCategoryEnum.ORDER,
            priority=NotificationPriorityEnum.HIGH,
            expiry_date=self.now + timedelta(days=7),
        )

        self.active_notification_2 = NotificationFactory(
            kind=NotificationKindEnum.SUCCESS,
            category=NotificationCategoryEnum.CART,
            priority=NotificationPriorityEnum.NORMAL,
            expiry_date=None,
        )

        self.expired_notification = NotificationFactory(
            kind=NotificationKindEnum.WARNING,
            category=NotificationCategoryEnum.SECURITY,
            priority=NotificationPriorityEnum.URGENT,
            expiry_date=self.now - timedelta(days=1),
        )

        self.critical_notification = NotificationFactory(
            kind=NotificationKindEnum.ERROR,
            category=NotificationCategoryEnum.SYSTEM,
            priority=NotificationPriorityEnum.CRITICAL,
            expiry_date=self.now + timedelta(days=30),
        )

        self.promo_notification = NotificationFactory(
            kind=NotificationKindEnum.INFO,
            category=NotificationCategoryEnum.PROMOTION,
            priority=NotificationPriorityEnum.LOW,
            link="https://example.com/promo",
        )

        self.active_notification_1.link = ""
        self.active_notification_1.save()

        self.active_notification_2.link = ""
        self.active_notification_2.save()

        self.expired_notification.link = ""
        self.expired_notification.save()

        self.critical_notification.link = ""
        self.critical_notification.save()

        old_date = self.now - timedelta(days=100)
        self.old_expired_notification = NotificationFactory(
            kind=NotificationKindEnum.WARNING,
            category=NotificationCategoryEnum.ACCOUNT,
            priority=NotificationPriorityEnum.NORMAL,
            expiry_date=self.now - timedelta(days=95),
        )
        Notification.objects.filter(id=self.old_expired_notification.id).update(
            created_at=old_date
        )

    def test_active_notifications(self):
        active_notifications = Notification.objects.active()

        self.assertIn(self.active_notification_1, active_notifications)
        self.assertIn(self.active_notification_2, active_notifications)
        self.assertIn(self.critical_notification, active_notifications)
        self.assertIn(self.promo_notification, active_notifications)
        self.assertNotIn(self.expired_notification, active_notifications)
        self.assertNotIn(self.old_expired_notification, active_notifications)

    def test_expired_notifications(self):
        expired_notifications = Notification.objects.expired()

        self.assertIn(self.expired_notification, expired_notifications)
        self.assertIn(self.old_expired_notification, expired_notifications)
        self.assertNotIn(self.active_notification_1, expired_notifications)
        self.assertNotIn(self.active_notification_2, expired_notifications)

    def test_by_kind_filtering(self):
        info_notifications = Notification.objects.by_kind(
            NotificationKindEnum.INFO
        )
        error_notifications = Notification.objects.by_kind(
            NotificationKindEnum.ERROR
        )

        self.assertIn(self.active_notification_1, info_notifications)
        self.assertIn(self.promo_notification, info_notifications)
        self.assertNotIn(self.critical_notification, info_notifications)

        self.assertIn(self.critical_notification, error_notifications)
        self.assertNotIn(self.active_notification_1, error_notifications)

    def test_by_category_filtering(self):
        order_notifications = Notification.objects.by_category(
            NotificationCategoryEnum.ORDER
        )
        system_notifications = Notification.objects.by_category(
            NotificationCategoryEnum.SYSTEM
        )

        self.assertIn(self.active_notification_1, order_notifications)
        self.assertNotIn(self.active_notification_2, order_notifications)

        self.assertIn(self.critical_notification, system_notifications)
        self.assertNotIn(self.active_notification_1, system_notifications)

    def test_by_priority_filtering(self):
        high_priority = Notification.objects.by_priority(
            NotificationPriorityEnum.HIGH
        )
        critical_priority = Notification.objects.by_priority(
            NotificationPriorityEnum.CRITICAL
        )

        self.assertIn(self.active_notification_1, high_priority)
        self.assertNotIn(self.active_notification_2, high_priority)

        self.assertIn(self.critical_notification, critical_priority)
        self.assertNotIn(self.active_notification_1, critical_priority)

    def test_high_priority_notifications(self):
        high_priority_notifications = Notification.objects.high_priority()

        self.assertIn(self.active_notification_1, high_priority_notifications)
        self.assertIn(self.expired_notification, high_priority_notifications)
        self.assertIn(self.critical_notification, high_priority_notifications)
        self.assertNotIn(
            self.active_notification_2, high_priority_notifications
        )
        self.assertNotIn(self.promo_notification, high_priority_notifications)

    def test_recent_notifications(self):
        recent_notifications = Notification.objects.recent(days=7)

        self.assertIn(self.active_notification_1, recent_notifications)
        self.assertIn(self.active_notification_2, recent_notifications)
        self.assertIn(self.expired_notification, recent_notifications)
        self.assertIn(self.critical_notification, recent_notifications)
        self.assertIn(self.promo_notification, recent_notifications)
        self.assertNotIn(self.old_expired_notification, recent_notifications)

    def test_by_date_range_filtering(self):
        start_date = (self.now - timedelta(days=1)).date()
        end_date = (self.now + timedelta(days=1)).date()

        date_range_notifications = Notification.objects.by_date_range(
            start_date, end_date
        )

        self.assertIn(self.active_notification_1, date_range_notifications)
        self.assertIn(self.active_notification_2, date_range_notifications)
        self.assertNotIn(
            self.old_expired_notification, date_range_notifications
        )

    def test_with_translations_prefetch(self):
        notifications_with_translations = (
            Notification.objects.with_translations()
        )

        self.assertGreater(len(notifications_with_translations), 0)
        for notification in notifications_with_translations:
            self.assertIsNotNone(
                notification.safe_translation_getter("title", any_language=True)
            )

    def test_system_notifications(self):
        system_notifications = Notification.objects.system_notifications()

        self.assertIn(self.critical_notification, system_notifications)
        self.assertNotIn(self.active_notification_1, system_notifications)
        self.assertNotIn(self.promo_notification, system_notifications)

    def test_user_notifications(self):
        user_notifications = Notification.objects.user_notifications()

        self.assertIn(self.active_notification_1, user_notifications)
        self.assertIn(self.promo_notification, user_notifications)
        self.assertNotIn(self.critical_notification, user_notifications)

    def test_category_specific_filters(self):
        order_notifications = Notification.objects.order_related()
        cart_notifications = Notification.objects.cart_related()
        security_notifications = Notification.objects.security_related()
        promotional_notifications = Notification.objects.promotional()

        self.assertIn(self.active_notification_1, order_notifications)
        self.assertIn(self.active_notification_2, cart_notifications)
        self.assertIn(self.expired_notification, security_notifications)
        self.assertIn(self.promo_notification, promotional_notifications)

    def test_with_link_filtering(self):
        notifications_with_links = Notification.objects.with_link()

        self.assertIn(self.promo_notification, notifications_with_links)
        notifications_without_links = [
            self.active_notification_1,
            self.active_notification_2,
            self.expired_notification,
            self.critical_notification,
        ]
        for notification in notifications_without_links:
            self.assertNotIn(notification, notifications_with_links)

    def test_scheduled_for_today(self):
        today_notifications = Notification.objects.scheduled_for_today()
        self.assertIsInstance(
            today_notifications, type(Notification.objects.all())
        )

        self.assertNotIn(self.old_expired_notification, today_notifications)

        today = timezone.now().date()
        manual_filter = Notification.objects.filter(created_at__date=today)

        self.assertEqual(today_notifications.count(), manual_filter.count())

    def test_needs_cleanup(self):
        cleanup_notifications = Notification.objects.needs_cleanup(days_old=90)

        self.assertIn(self.old_expired_notification, cleanup_notifications)
        self.assertNotIn(self.expired_notification, cleanup_notifications)
        self.assertNotIn(self.active_notification_1, cleanup_notifications)

    def test_chained_filtering(self):
        high_priority_active = Notification.objects.active().high_priority()

        self.assertIn(self.active_notification_1, high_priority_active)
        self.assertIn(self.critical_notification, high_priority_active)
        self.assertNotIn(self.expired_notification, high_priority_active)


@pytest.mark.django_db
class TestNotificationManager(TestCase):
    def setUp(self):
        self.now = timezone.now()

        self.active_notification = NotificationFactory(
            kind=NotificationKindEnum.INFO,
            priority=NotificationPriorityEnum.HIGH,
            expiry_date=self.now + timedelta(days=7),
        )

        self.expired_notification = NotificationFactory(
            kind=NotificationKindEnum.WARNING,
            priority=NotificationPriorityEnum.URGENT,
            expiry_date=self.now - timedelta(days=1),
        )

        self.critical_notification = NotificationFactory(
            kind=NotificationKindEnum.ERROR,
            priority=NotificationPriorityEnum.CRITICAL,
            expiry_date=self.now + timedelta(days=30),
        )

        old_date = self.now - timedelta(days=100)
        self.old_expired_notification = NotificationFactory(
            kind=NotificationKindEnum.WARNING,
            priority=NotificationPriorityEnum.NORMAL,
            expiry_date=self.now - timedelta(days=95),
        )
        Notification.objects.filter(id=self.old_expired_notification.id).update(
            created_at=old_date
        )

    def test_manager_delegation_methods(self):
        active_notifications = Notification.objects.active()
        high_priority_notifications = Notification.objects.high_priority()
        recent_notifications = Notification.objects.recent(days=7)

        self.assertIn(self.active_notification, active_notifications)
        self.assertIn(self.active_notification, high_priority_notifications)
        self.assertIn(self.active_notification, recent_notifications)

    def test_cleanup_expired_method(self):
        initial_count = Notification.objects.count()

        deleted_count = Notification.objects.cleanup_expired(days_old=90)

        self.assertEqual(deleted_count, 1)
        self.assertEqual(Notification.objects.count(), initial_count - 1)

        with self.assertRaises(Notification.DoesNotExist):
            Notification.objects.get(id=self.old_expired_notification.id)

        self.assertTrue(
            Notification.objects.filter(id=self.active_notification.id).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                id=self.expired_notification.id
            ).exists()
        )

    def test_get_notifications_needing_attention(self):
        attention_notifications = (
            Notification.objects.get_notifications_needing_attention()
        )

        self.assertIn(self.critical_notification, attention_notifications)
        self.assertNotIn(self.active_notification, attention_notifications)
        self.assertNotIn(self.expired_notification, attention_notifications)

    def test_bulk_mark_expired(self):
        fresh_expired = NotificationFactory(
            kind=NotificationKindEnum.ERROR,
            priority=NotificationPriorityEnum.NORMAL,
            expiry_date=self.now - timedelta(days=1),
        )

        self.assertEqual(fresh_expired.kind, NotificationKindEnum.ERROR)

        updated_count = Notification.objects.bulk_mark_expired()

        self.assertGreater(updated_count, 0)

        fresh_expired.refresh_from_db()
        self.old_expired_notification.refresh_from_db()

        self.assertEqual(fresh_expired.kind, NotificationKindEnum.WARNING)
        self.assertEqual(
            self.old_expired_notification.kind, NotificationKindEnum.WARNING
        )

        self.active_notification.refresh_from_db()
        self.assertNotEqual(
            self.active_notification.kind, NotificationKindEnum.WARNING
        )

    def test_manager_queryset_type(self):
        queryset = Notification.objects.get_queryset()
        self.assertIsInstance(queryset, NotificationQuerySet)

    def test_complex_filtering_combinations(self):
        active_high_priority = Notification.objects.active().high_priority()

        self.assertIn(self.active_notification, active_high_priority)
        self.assertIn(self.critical_notification, active_high_priority)
        self.assertNotIn(self.expired_notification, active_high_priority)

        recent_notifications = Notification.objects.recent(days=7)

        expected_recent = [
            self.active_notification,
            self.expired_notification,
            self.critical_notification,
        ]
        for notification in expected_recent:
            self.assertIn(notification, recent_notifications)

        self.assertNotIn(self.old_expired_notification, recent_notifications)
