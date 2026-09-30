from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone
from django.utils.translation import override as translation_override

from notification.admin import (
    NotificationAdmin,
    NotificationStatusFilter,
    NotificationUserAdmin,
    NotificationUserStatusFilter,
)
from notification.models.notification import Notification
from notification.models.user import NotificationUser

pytestmark = pytest.mark.assert_english

User = get_user_model()


class NotificationStatusFilterTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.notification_active = Notification.objects.create(
            title="Active Notification",
            message="Active message",
            kind="INFO",
            category="SYSTEM",
            priority="NORMAL",
            notification_type="general",
            expiry_date=timezone.now() + timedelta(days=7),
        )

        cls.notification_expired = Notification.objects.create(
            title="Expired Notification",
            message="Expired message",
            kind="WARNING",
            category="ORDER",
            priority="HIGH",
            notification_type="order_status",
            expiry_date=timezone.now() - timedelta(days=1),
        )

        cls.notification_urgent = Notification.objects.create(
            title="Urgent Notification",
            message="Urgent message",
            kind="ERROR",
            category="SECURITY",
            priority="URGENT",
            notification_type="security_alert",
        )

        cls.notification_with_link = Notification.objects.create(
            title="Notification with Link",
            message="Link message",
            kind="INFO",
            category="PROMOTION",
            priority="NORMAL",
            notification_type="promotion",
            link="/",
        )

    def setUp(self):
        self.request = RequestFactory().get("/")
        self.request.user = Mock()
        self.model_admin = Mock()
        self.filter = NotificationStatusFilter(
            self.request, {}, Notification, self.model_admin
        )

    def test_lookups(self):
        # ``lookups`` returns ``(key, lazy_translated_label)`` pairs —
        # label contents depend on the active locale (Greek by default
        # in this test suite). Force English so the literal assertions
        # stay deterministic without making the test locale-dependent.
        with translation_override("en"):
            lookups = self.filter.lookups(self.request, self.model_admin)
            lookup_pairs = [(key, str(label)) for key, label in lookups]

        self.assertEqual(len(lookup_pairs), 10)
        self.assertIn(("active", "Active"), lookup_pairs)
        self.assertIn(("expired", "Expired"), lookup_pairs)
        self.assertIn(("urgent", "Urgent Priority"), lookup_pairs)
        self.assertIn(("recent", "Recent (Last 7 days)"), lookup_pairs)
        self.assertIn(("with_link", "Has Link"), lookup_pairs)
        self.assertIn(("system", "System Notifications"), lookup_pairs)
        self.assertIn(("order", "Order Related"), lookup_pairs)
        self.assertIn(("payment", "Payment Related"), lookup_pairs)
        self.assertIn(("security", "Security Related"), lookup_pairs)
        self.assertIn(("promotion", "Promotions"), lookup_pairs)

    def test_queryset_active(self):
        self.filter.used_parameters = {"notification_status": "active"}
        queryset = Notification.objects.all()

        result = self.filter.queryset(self.request, queryset)

        self.assertIn(self.notification_active, result)
        self.assertNotIn(self.notification_expired, result)

    def test_queryset_expired(self):
        self.filter.used_parameters = {"notification_status": "expired"}
        queryset = Notification.objects.all()

        result = self.filter.queryset(self.request, queryset)

        self.assertIn(self.notification_expired, result)
        self.assertNotIn(self.notification_active, result)

    def test_queryset_urgent(self):
        self.filter.used_parameters = {"notification_status": "urgent"}
        queryset = Notification.objects.all()

        result = self.filter.queryset(self.request, queryset)

        self.assertIn(self.notification_urgent, result)
        self.assertNotIn(self.notification_active, result)

    def test_queryset_with_link(self):
        self.filter.used_parameters = {"notification_status": "with_link"}
        queryset = Notification.objects.all()

        result = self.filter.queryset(self.request, queryset)

        self.assertIn(self.notification_with_link, result)
        self.assertNotIn(self.notification_active, result)

    def test_queryset_by_category(self):
        test_cases = [
            ("system", self.notification_active),
            ("order", self.notification_expired),
            ("security", self.notification_urgent),
            ("promotion", self.notification_with_link),
        ]

        for filter_value, expected_notification in test_cases:
            with self.subTest(filter_value=filter_value):
                self.filter.used_parameters = {
                    "notification_status": filter_value
                }
                queryset = Notification.objects.all()

                result = self.filter.queryset(self.request, queryset)

                self.assertIn(expected_notification, result)

    def test_queryset_payment_selects_only_payment_notifications(self):
        payment = Notification.objects.create(
            title="Payment Notification",
            message="Payment message",
            kind="INFO",
            category="PAYMENT",
            priority="NORMAL",
            notification_type="payment",
        )
        self.filter.used_parameters = {"notification_status": "payment"}

        result = self.filter.queryset(self.request, Notification.objects.all())

        self.assertEqual(list(result), [payment])

    def test_queryset_recent(self):
        self.filter.used_parameters = {"notification_status": "recent"}
        queryset = Notification.objects.all()

        result = self.filter.queryset(self.request, queryset)

        self.assertTrue(result.exists())

    def test_queryset_default(self):
        self.filter.used_parameters = {"notification_status": "invalid"}
        queryset = Notification.objects.all()

        result = self.filter.queryset(self.request, queryset)

        self.assertEqual(list(result), list(queryset))


class NotificationUserStatusFilterTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )

        cls.notification = Notification.objects.create(
            title="Test Notification",
            message="Test message",
            kind="INFO",
            category="SYSTEM",
            priority="NORMAL",
            notification_type="general",
        )

        cls.notification_urgent = Notification.objects.create(
            title="Urgent Notification",
            message="Urgent message",
            kind="ERROR",
            category="SECURITY",
            priority="URGENT",
            notification_type="security_alert",
        )

        cls.notification_user_seen = NotificationUser.objects.create(
            user=cls.user,
            notification=cls.notification,
            seen=True,
            seen_at=timezone.now(),
        )

        cls.notification_user_unseen = NotificationUser.objects.create(
            user=cls.user, notification=cls.notification_urgent, seen=False
        )

    def setUp(self):
        self.request = RequestFactory().get("/")
        self.request.user = Mock()
        self.model_admin = Mock()
        self.filter = NotificationUserStatusFilter(
            self.request, {}, NotificationUser, self.model_admin
        )

    def test_lookups(self):
        lookups = self.filter.lookups(self.request, self.model_admin)

        self.assertEqual(len(lookups), 5)
        self.assertIn(("seen", "Seen"), lookups)
        self.assertIn(("unseen", "Unseen"), lookups)
        self.assertIn(("recent_seen", "Seen Today"), lookups)
        self.assertIn(("urgent_unseen", "Urgent & Unseen"), lookups)
        self.assertIn(("expired", "Expired Notifications"), lookups)

    def test_queryset_seen(self):
        self.filter.used_parameters = {"user_status": "seen"}
        queryset = NotificationUser.objects.all()

        result = self.filter.queryset(self.request, queryset)

        self.assertIn(self.notification_user_seen, result)
        self.assertNotIn(self.notification_user_unseen, result)

    def test_queryset_unseen(self):
        self.filter.used_parameters = {"user_status": "unseen"}
        queryset = NotificationUser.objects.all()

        result = self.filter.queryset(self.request, queryset)

        self.assertIn(self.notification_user_unseen, result)
        self.assertNotIn(self.notification_user_seen, result)

    def test_queryset_urgent_unseen(self):
        self.filter.used_parameters = {"user_status": "urgent_unseen"}
        queryset = NotificationUser.objects.all()

        result = self.filter.queryset(self.request, queryset)

        self.assertIn(self.notification_user_unseen, result)
        self.assertNotIn(self.notification_user_seen, result)

    def test_queryset_default(self):
        self.filter.used_parameters = {"user_status": "invalid"}
        queryset = NotificationUser.objects.all()

        result = self.filter.queryset(self.request, queryset)

        self.assertEqual(list(result), list(queryset))


class NotificationAdminTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.notification = Notification.objects.create(
            title="Test Notification",
            message="Test notification message",
            kind="INFO",
            category="SYSTEM",
            priority="HIGH",
            notification_type="general",
            link="/",
            expiry_date=timezone.now() + timedelta(days=7),
        )

        cls.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )

        cls.notification_user = NotificationUser.objects.create(
            user=cls.user,
            notification=cls.notification,
            seen=True,
            seen_at=timezone.now(),
        )

    def setUp(self):
        self.factory = RequestFactory()
        self.admin = NotificationAdmin(Notification, AdminSite())

    def test_get_queryset(self):
        request = self.factory.get("/")
        request.user = Mock()

        queryset = self.admin.get_queryset(request)

        self.assertTrue(hasattr(queryset, "_prefetch_related_lookups"))
        # ``Notification`` has no ``user`` FK (it's a broadcast model;
        # per-user delivery lives on ``UserNotification`` via the
        # ``notification_users`` reverse manager). The historical
        # ``prefetch_related("user__user")`` raised a 500 on every
        # changelist load — replaced with the translations prefetch
        # that's actually used by the per-row title/message display.
        self.assertIn("translations", queryset._prefetch_related_lookups)
        self.assertNotIn("user__user", queryset._prefetch_related_lookups)

    def test_notification_info(self):
        result = self.admin.notification_info(self.notification)

        self.assertIn("Test Notification", result)
        self.assertIn("Test notification message", result)

    def test_kind_label(self):
        result = self.admin.kind_label(self.notification)
        self.assertEqual(result, ("INFO", "Info"))

    def test_category_label(self):
        result = self.admin.category_label(self.notification)
        self.assertEqual(result, ("SYSTEM", "System"))

    def test_priority_label(self):
        result = self.admin.priority_label(self.notification)
        self.assertEqual(result, ("HIGH", "High Priority"))

    def test_expiry_status(self):
        result = self.admin.expiry_status(self.notification)
        self.assertEqual(result, ("active", "Active"))

        self.notification.expiry_date = timezone.now() - timedelta(days=1)
        result = self.admin.expiry_status(self.notification)
        self.assertEqual(result, ("expired", "Expired"))

        self.notification.expiry_date = None
        result = self.admin.expiry_status(self.notification)
        self.assertEqual(result, ("active", "Active"))

    def test_engagement_stats(self):
        result = self.admin.engagement_stats(self.notification)

        self.assertIn("1/1", result)
        self.assertIn("%", result)

    def test_timing_info(self):
        result = self.admin.timing_info(self.notification)

        self.assertTrue(len(result) > 0)
        import re

        self.assertTrue(re.search(r"\d{2}/\d{2}", result))

        self.notification.expiry_date = timezone.now() - timedelta(days=1)
        result = self.admin.timing_info(self.notification)
        self.assertIn("expired", result)

        self.notification.expiry_date = None
        result = self.admin.timing_info(self.notification)
        self.assertIn("no expiry", result)

    def test_notification_analytics(self):
        result = self.admin.notification_analytics(self.notification)

        self.assertIn("Title", result)
        self.assertIn("Message", result)
        self.assertIn("chars", result)
        self.assertIn("Yes", result)

    def test_notification_analytics_unsaved(self):
        unsaved = Notification(
            title="Draft", message="Draft message", kind="INFO"
        )
        result = self.admin.notification_analytics(unsaved)
        self.assertEqual(result, "Available after creation.")

    def test_engagement_summary(self):
        result = self.admin.engagement_summary(self.notification)

        self.assertIn("1 seen", result)
        self.assertIn("0 unseen", result)
        self.assertIn("%", result)

    def test_timing_summary(self):
        result = self.admin.timing_summary(self.notification)

        self.assertIn("Created", result)
        self.assertIn("Expires", result)
        self.assertIn("Active", result)

    def test_timing_summary_unsaved(self):
        # Regression test: this crashed with ``TypeError`` (``now -
        # obj.created_at`` where ``created_at`` is ``None``) on the
        # add form's readonly "Timing Information" fieldset.
        unsaved = Notification(title="Draft", message="Draft message")
        result = self.admin.timing_summary(unsaved)
        self.assertEqual(result, "Available after creation.")


class NotificationUserAdminTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )

        cls.notification = Notification.objects.create(
            title="Test Notification",
            message="Test message",
            kind="INFO",
            category="SYSTEM",
            priority="HIGH",
            notification_type="general",
        )

        cls.notification_user = NotificationUser.objects.create(
            user=cls.user,
            notification=cls.notification,
            seen=True,
            seen_at=timezone.now(),
        )

    def setUp(self):
        self.admin = NotificationUserAdmin(NotificationUser, AdminSite())

    def test_user_info(self):
        result = self.admin.user_info(self.notification_user)

        self.assertEqual(result[0], "testuser")
        self.assertEqual(result[1], "test@example.com")

    def test_notification_info(self):
        result = self.admin.notification_info(self.notification_user)

        self.assertIn("Test Notification", result)
        self.assertIn("Info", result)

    def test_seen_label(self):
        result = self.admin.seen_label(self.notification_user)
        self.assertEqual(result, ("seen", "Seen"))

        self.notification_user.seen = False
        self.notification_user.seen_at = None
        result = self.admin.seen_label(self.notification_user)
        self.assertEqual(result, ("unseen", "Unseen"))

    def test_priority_label(self):
        result = self.admin.priority_label(self.notification_user)
        self.assertEqual(result, ("HIGH", "High Priority"))

    def test_timing_display(self):
        result = self.admin.timing_display(self.notification_user)

        self.assertTrue(len(result) > 0)
        self.assertIn("Active", result)

    def test_user_notification_analytics(self):
        result = self.admin.user_notification_analytics(self.notification_user)

        self.assertIn("Response time", result)
        self.assertIn("Notification", result)
        self.assertIn("High Priority", result)
        self.assertIn("System", result)

    def test_user_notification_analytics_unsaved(self):
        unsaved = NotificationUser(
            user=self.user, notification=self.notification
        )
        result = self.admin.user_notification_analytics(unsaved)
        self.assertEqual(result, "Available after creation.")
