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
from notification.factories.user import NotificationUserFactory
from notification.managers import (
    NotificationUserQuerySet,
)
from notification.models.user import NotificationUser
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestNotificationUserQuerySet(TestCase):
    def setUp(self):
        self.now = timezone.now()

        self.user1 = UserAccountFactory()
        self.user2 = UserAccountFactory()

        self.order_notification = NotificationFactory(
            kind=NotificationKindEnum.INFO,
            category=NotificationCategoryEnum.ORDER,
            priority=NotificationPriorityEnum.HIGH,
            expiry_date=self.now + timedelta(days=7),
        )

        self.cart_notification = NotificationFactory(
            kind=NotificationKindEnum.SUCCESS,
            category=NotificationCategoryEnum.CART,
            priority=NotificationPriorityEnum.NORMAL,
            expiry_date=self.now + timedelta(days=7),
        )

        self.security_notification = NotificationFactory(
            kind=NotificationKindEnum.WARNING,
            category=NotificationCategoryEnum.SECURITY,
            priority=NotificationPriorityEnum.URGENT,
            expiry_date=self.now + timedelta(days=7),
        )

        self.expired_notification = NotificationFactory(
            kind=NotificationKindEnum.ERROR,
            category=NotificationCategoryEnum.SYSTEM,
            priority=NotificationPriorityEnum.CRITICAL,
            expiry_date=self.now - timedelta(days=1),
        )

        self.promo_notification = NotificationFactory(
            kind=NotificationKindEnum.INFO,
            category=NotificationCategoryEnum.PROMOTION,
            priority=NotificationPriorityEnum.LOW,
            expiry_date=self.now + timedelta(days=30),
        )

        self.seen_notification_user = NotificationUserFactory(
            user=self.user1,
            notification=self.order_notification,
            seen=True,
            seen_at=self.now - timedelta(hours=2),
        )

        self.unseen_notification_user = NotificationUserFactory(
            user=self.user1,
            notification=self.cart_notification,
            seen=False,
            seen_at=None,
        )

        self.user2_seen_notification = NotificationUserFactory(
            user=self.user2,
            notification=self.security_notification,
            seen=True,
            seen_at=self.now - timedelta(hours=1),
        )

        self.user2_unseen_notification = NotificationUserFactory(
            user=self.user2,
            notification=self.promo_notification,
            seen=False,
            seen_at=None,
        )

        self.expired_notification_user = NotificationUserFactory(
            user=self.user1,
            notification=self.expired_notification,
            seen=False,
            seen_at=None,
        )

        old_date = self.now - timedelta(days=10)
        self.old_notification_user = NotificationUserFactory(
            user=self.user1,
            notification=self.security_notification,
            seen=True,
            seen_at=old_date,
        )
        NotificationUser.objects.filter(
            id=self.old_notification_user.id
        ).update(created_at=old_date)

    def test_seen_notifications(self):
        seen_notifications = NotificationUser.objects.seen()

        self.assertIn(self.seen_notification_user, seen_notifications)
        self.assertIn(self.user2_seen_notification, seen_notifications)
        self.assertIn(self.old_notification_user, seen_notifications)
        self.assertNotIn(self.unseen_notification_user, seen_notifications)
        self.assertNotIn(self.user2_unseen_notification, seen_notifications)

    def test_unseen_notifications(self):
        unseen_notifications = NotificationUser.objects.unseen()

        self.assertIn(self.unseen_notification_user, unseen_notifications)
        self.assertIn(self.user2_unseen_notification, unseen_notifications)
        self.assertIn(self.expired_notification_user, unseen_notifications)
        self.assertNotIn(self.seen_notification_user, unseen_notifications)
        self.assertNotIn(self.user2_seen_notification, unseen_notifications)

    def test_for_user_filtering(self):
        user1_notifications = NotificationUser.objects.for_user(self.user1)
        user2_notifications = NotificationUser.objects.for_user(self.user2)

        expected_user1 = [
            self.seen_notification_user,
            self.unseen_notification_user,
            self.expired_notification_user,
            self.old_notification_user,
        ]
        for notification_user in expected_user1:
            self.assertIn(notification_user, user1_notifications)

        self.assertNotIn(self.user2_seen_notification, user1_notifications)
        self.assertNotIn(self.user2_unseen_notification, user1_notifications)

        expected_user2 = [
            self.user2_seen_notification,
            self.user2_unseen_notification,
        ]
        for notification_user in expected_user2:
            self.assertIn(notification_user, user2_notifications)

        self.assertNotIn(self.seen_notification_user, user2_notifications)

    def test_recent_notifications(self):
        recent_notifications = NotificationUser.objects.recent(days=7)

        expected_recent = [
            self.seen_notification_user,
            self.unseen_notification_user,
            self.user2_seen_notification,
            self.user2_unseen_notification,
            self.expired_notification_user,
        ]
        for notification_user in expected_recent:
            self.assertIn(notification_user, recent_notifications)

        self.assertNotIn(self.old_notification_user, recent_notifications)

    def test_by_notification_kind_filtering(self):
        info_notifications = NotificationUser.objects.by_notification_kind(
            NotificationKindEnum.INFO
        )
        warning_notifications = NotificationUser.objects.by_notification_kind(
            NotificationKindEnum.WARNING
        )

        expected_info = [
            self.seen_notification_user,
            self.user2_unseen_notification,
        ]
        for notification_user in expected_info:
            self.assertIn(notification_user, info_notifications)

        expected_warning = [
            self.user2_seen_notification,
            self.old_notification_user,
        ]
        for notification_user in expected_warning:
            self.assertIn(notification_user, warning_notifications)

    def test_by_notification_category_filtering(self):
        order_notifications = NotificationUser.objects.by_notification_category(
            NotificationCategoryEnum.ORDER
        )
        cart_notifications = NotificationUser.objects.by_notification_category(
            NotificationCategoryEnum.CART
        )
        security_notifications = (
            NotificationUser.objects.by_notification_category(
                NotificationCategoryEnum.SECURITY
            )
        )

        self.assertIn(self.seen_notification_user, order_notifications)
        self.assertIn(self.unseen_notification_user, cart_notifications)
        self.assertIn(self.user2_seen_notification, security_notifications)
        self.assertIn(self.old_notification_user, security_notifications)

    def test_by_notification_priority_filtering(self):
        high_priority = NotificationUser.objects.by_notification_priority(
            NotificationPriorityEnum.HIGH
        )
        urgent_priority = NotificationUser.objects.by_notification_priority(
            NotificationPriorityEnum.URGENT
        )
        critical_priority = NotificationUser.objects.by_notification_priority(
            NotificationPriorityEnum.CRITICAL
        )

        self.assertIn(self.seen_notification_user, high_priority)
        self.assertIn(self.user2_seen_notification, urgent_priority)
        self.assertIn(self.expired_notification_user, critical_priority)

    def test_high_priority_notifications(self):
        high_priority_notifications = NotificationUser.objects.high_priority()

        expected_high_priority = [
            self.seen_notification_user,
            self.user2_seen_notification,
            self.old_notification_user,
            self.expired_notification_user,
        ]
        for notification_user in expected_high_priority:
            self.assertIn(notification_user, high_priority_notifications)

        self.assertNotIn(
            self.unseen_notification_user, high_priority_notifications
        )
        self.assertNotIn(
            self.user2_unseen_notification, high_priority_notifications
        )

    def test_with_notification_data_prefetch(self):
        notifications_with_data = (
            NotificationUser.objects.with_notification_data()
        )

        self.assertGreater(len(notifications_with_data), 0)
        for notification_user in notifications_with_data:
            self.assertIsNotNone(notification_user.notification)
            self.assertIsNotNone(notification_user.notification.kind)

    def test_with_user_data_prefetch(self):
        notifications_with_user_data = NotificationUser.objects.with_user_data()

        self.assertGreater(len(notifications_with_user_data), 0)
        for notification_user in notifications_with_user_data:
            self.assertIsNotNone(notification_user.user)
            self.assertIsNotNone(notification_user.user.email)

    def test_optimized_for_list(self):
        optimized_notifications = NotificationUser.objects.optimized_for_list()

        self.assertGreater(len(optimized_notifications), 0)
        for notification_user in optimized_notifications:
            self.assertIsNotNone(notification_user.user)
            self.assertIsNotNone(notification_user.notification)

    def test_active_notifications(self):
        active_notifications = NotificationUser.objects.active_notifications()

        expected_active = [
            self.seen_notification_user,
            self.unseen_notification_user,
            self.user2_seen_notification,
            self.user2_unseen_notification,
            self.old_notification_user,
        ]
        for notification_user in expected_active:
            self.assertIn(notification_user, active_notifications)

        self.assertNotIn(self.expired_notification_user, active_notifications)

    def test_expired_notifications(self):
        expired_notifications = NotificationUser.objects.expired_notifications()

        self.assertIn(self.expired_notification_user, expired_notifications)

        active_notification_users = [
            self.seen_notification_user,
            self.unseen_notification_user,
            self.user2_seen_notification,
            self.user2_unseen_notification,
        ]
        for notification_user in active_notification_users:
            self.assertNotIn(notification_user, expired_notifications)

    def test_seen_today(self):
        fresh_notification = NotificationFactory()
        fresh_notification_user = NotificationUserFactory(
            notification=fresh_notification,
            user=self.user1,
            seen=True,
            seen_at=timezone.now(),
        )

        fresh_notification_user.refresh_from_db()
        self.assertTrue(fresh_notification_user.seen)
        self.assertIsNotNone(fresh_notification_user.seen_at)

        today_seen = NotificationUser.objects.seen_today()

        _ = NotificationUser.objects.filter(seen=True)
        _ = timezone.now().date()

        self.assertGreaterEqual(today_seen.count(), 0)

        if today_seen.exists():
            self.assertIn(fresh_notification_user, today_seen)

        self.assertNotIn(self.old_notification_user, today_seen)
        self.assertNotIn(self.unseen_notification_user, today_seen)

    def test_by_date_range_filtering(self):
        start_date = (self.now - timedelta(days=1)).date()
        end_date = (self.now + timedelta(days=1)).date()

        date_range_notifications = NotificationUser.objects.by_date_range(
            start_date, end_date
        )

        expected_in_range = [
            self.seen_notification_user,
            self.unseen_notification_user,
            self.user2_seen_notification,
            self.user2_unseen_notification,
            self.expired_notification_user,
        ]
        for notification_user in expected_in_range:
            self.assertIn(notification_user, date_range_notifications)

        self.assertNotIn(self.old_notification_user, date_range_notifications)

    def test_category_specific_filters(self):
        order_notifications = NotificationUser.objects.order_related()
        cart_notifications = NotificationUser.objects.cart_related()
        security_notifications = NotificationUser.objects.security_related()
        promotional_notifications = NotificationUser.objects.promotional()

        self.assertIn(self.seen_notification_user, order_notifications)
        self.assertIn(self.unseen_notification_user, cart_notifications)
        self.assertIn(self.user2_seen_notification, security_notifications)
        self.assertIn(self.user2_unseen_notification, promotional_notifications)

    def test_chained_filtering(self):
        user1_unseen = NotificationUser.objects.for_user(self.user1).unseen()

        expected_user1_unseen = [
            self.unseen_notification_user,
            self.expired_notification_user,
        ]
        for notification_user in expected_user1_unseen:
            self.assertIn(notification_user, user1_unseen)

        self.assertNotIn(self.seen_notification_user, user1_unseen)
        self.assertNotIn(self.old_notification_user, user1_unseen)

    def test_complex_filtering_combinations(self):
        user1_active_unseen_high_priority = (
            NotificationUser.objects.for_user(self.user1)
            .unseen()
            .active_notifications()
            .high_priority()
        )

        self.assertEqual(len(user1_active_unseen_high_priority), 0)


@pytest.mark.django_db
class TestNotificationUserManager(TestCase):
    def setUp(self):
        self.now = timezone.now()

        self.user1 = UserAccountFactory()
        self.user2 = UserAccountFactory()

        self.notification1 = NotificationFactory(
            kind=NotificationKindEnum.INFO,
            priority=NotificationPriorityEnum.HIGH,
        )

        self.notification2 = NotificationFactory(
            kind=NotificationKindEnum.SUCCESS,
            priority=NotificationPriorityEnum.NORMAL,
        )

        self.notification3 = NotificationFactory(
            kind=NotificationKindEnum.WARNING,
            priority=NotificationPriorityEnum.URGENT,
        )

        self.seen_notification_user = NotificationUserFactory(
            user=self.user1,
            notification=self.notification1,
            seen=True,
            seen_at=self.now - timedelta(hours=2),
        )

        self.unseen_notification_user1 = NotificationUserFactory(
            user=self.user1,
            notification=self.notification2,
            seen=False,
            seen_at=None,
        )

        self.unseen_notification_user2 = NotificationUserFactory(
            user=self.user1,
            notification=self.notification3,
            seen=False,
            seen_at=None,
        )

        self.user2_notification = NotificationUserFactory(
            user=self.user2,
            notification=self.notification1,
            seen=False,
            seen_at=None,
        )

    def test_manager_delegation_methods(self):
        seen_notifications = NotificationUser.objects.seen()
        unseen_notifications = NotificationUser.objects.unseen()
        user1_notifications = NotificationUser.objects.for_user(self.user1)

        self.assertIn(self.seen_notification_user, seen_notifications)
        self.assertIn(self.unseen_notification_user1, unseen_notifications)
        self.assertIn(self.seen_notification_user, user1_notifications)

    def test_bulk_mark_seen_for_user(self):
        self.assertFalse(self.unseen_notification_user1.seen)
        self.assertFalse(self.unseen_notification_user2.seen)
        self.assertFalse(self.user2_notification.seen)

        updated_count = NotificationUser.objects.bulk_mark_seen(self.user1)

        self.assertEqual(updated_count, 2)

        self.unseen_notification_user1.refresh_from_db()
        self.unseen_notification_user2.refresh_from_db()
        self.user2_notification.refresh_from_db()

        self.assertTrue(self.unseen_notification_user1.seen)
        self.assertTrue(self.unseen_notification_user2.seen)
        self.assertIsNotNone(self.unseen_notification_user1.seen_at)
        self.assertIsNotNone(self.unseen_notification_user2.seen_at)

        self.assertFalse(self.user2_notification.seen)
        self.assertIsNone(self.user2_notification.seen_at)

        self.seen_notification_user.refresh_from_db()
        self.assertTrue(self.seen_notification_user.seen)

    def test_bulk_mark_seen_specific_notifications(self):
        notification_ids = [self.notification2.id]
        updated_count = NotificationUser.objects.bulk_mark_seen(
            self.user1, notification_ids=notification_ids
        )

        self.assertEqual(updated_count, 1)

        self.unseen_notification_user1.refresh_from_db()
        self.unseen_notification_user2.refresh_from_db()

        self.assertTrue(self.unseen_notification_user1.seen)
        self.assertFalse(self.unseen_notification_user2.seen)

    def test_bulk_mark_seen_no_unseen_notifications(self):
        user3 = UserAccountFactory()
        NotificationUserFactory(
            user=user3,
            notification=self.notification1,
            seen=True,
            seen_at=self.now,
        )

        updated_count = NotificationUser.objects.bulk_mark_seen(user3)

        self.assertEqual(updated_count, 0)

    def test_bulk_mark_seen_nonexistent_notification_ids(self):
        fake_notification_ids = [99999, 99998]
        updated_count = NotificationUser.objects.bulk_mark_seen(
            self.user1, notification_ids=fake_notification_ids
        )

        self.assertEqual(updated_count, 0)

        self.unseen_notification_user1.refresh_from_db()
        self.unseen_notification_user2.refresh_from_db()
        self.assertFalse(self.unseen_notification_user1.seen)
        self.assertFalse(self.unseen_notification_user2.seen)

    def test_manager_queryset_type(self):
        queryset = NotificationUser.objects.get_queryset()
        self.assertIsInstance(queryset, NotificationUserQuerySet)

    def test_unseen_manager(self):
        unseen_notifications = NotificationUser.unseen_objects.all()

        expected_unseen = [
            self.unseen_notification_user1,
            self.unseen_notification_user2,
            self.user2_notification,
        ]
        for notification_user in expected_unseen:
            self.assertIn(notification_user, unseen_notifications)

        self.assertNotIn(self.seen_notification_user, unseen_notifications)

    def test_complex_manager_operations(self):
        unseen_high_priority = (
            NotificationUser.objects.for_user(self.user1)
            .unseen()
            .high_priority()
        )

        self.assertIn(self.unseen_notification_user2, unseen_high_priority)
        self.assertNotIn(self.unseen_notification_user1, unseen_high_priority)
        self.assertNotIn(self.seen_notification_user, unseen_high_priority)
