from datetime import timedelta
from unittest.mock import Mock

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from notification.admin import (
    NotificationAdmin,
    NotificationStatusFilter,
    NotificationUserAdmin,
    NotificationUserStatusFilter,
)
from notification.models.notification import Notification
from notification.models.user import NotificationUser

User = get_user_model()


class NotificationStatusFilterTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = Mock()

        self.model_admin = Mock()
        self.filter = NotificationStatusFilter(
            self.request, {}, Notification, self.model_admin
        )

        self.notification_active = Notification.objects.create(
            title="Active Notification",
            message="Active message",
            kind="INFO",
            category="SYSTEM",
            priority="NORMAL",
            notification_type="general",
            expiry_date=timezone.now() + timedelta(days=7),
        )

        self.notification_expired = Notification.objects.create(
            title="Expired Notification",
            message="Expired message",
            kind="WARNING",
            category="ORDER",
            priority="HIGH",
            notification_type="order_status",
            expiry_date=timezone.now() - timedelta(days=1),
        )

        self.notification_urgent = Notification.objects.create(
            title="Urgent Notification",
            message="Urgent message",
            kind="ERROR",
            category="SECURITY",
            priority="URGENT",
            notification_type="security_alert",
        )

        self.notification_with_link = Notification.objects.create(
            title="Notification with Link",
            message="Link message",
            kind="INFO",
            category="PROMOTION",
            priority="NORMAL",
            notification_type="promotion",
            link="https://example.com",
        )

    def test_lookups(self):
        lookups = self.filter.lookups(self.request, self.model_admin)

        self.assertEqual(len(lookups), 10)
        self.assertIn(("active", "Active"), lookups)
        self.assertIn(("expired", "Expired"), lookups)
        self.assertIn(("urgent", "Urgent Priority"), lookups)
        self.assertIn(("recent", "Recent (Last 7 days)"), lookups)
        self.assertIn(("with_link", "Has Link"), lookups)
        self.assertIn(("system", "System Notifications"), lookups)
        self.assertIn(("order", "Order Related"), lookups)
        self.assertIn(("payment", "Payment Related"), lookups)
        self.assertIn(("security", "Security Related"), lookups)
        self.assertIn(("promotion", "Promotions"), lookups)

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
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = Mock()

        self.model_admin = Mock()
        self.filter = NotificationUserStatusFilter(
            self.request, {}, NotificationUser, self.model_admin
        )

        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )

        self.notification = Notification.objects.create(
            title="Test Notification",
            message="Test message",
            kind="INFO",
            category="SYSTEM",
            priority="NORMAL",
            notification_type="general",
        )

        self.notification_urgent = Notification.objects.create(
            title="Urgent Notification",
            message="Urgent message",
            kind="ERROR",
            category="SECURITY",
            priority="URGENT",
            notification_type="security_alert",
        )

        self.notification_user_seen = NotificationUser.objects.create(
            user=self.user,
            notification=self.notification,
            seen=True,
            seen_at=timezone.now(),
        )

        self.notification_user_unseen = NotificationUser.objects.create(
            user=self.user, notification=self.notification_urgent, seen=False
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
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = NotificationAdmin(Notification, self.site)

        self.notification = Notification.objects.create(
            title="Test Notification",
            message="Test notification message",
            kind="INFO",
            category="SYSTEM",
            priority="HIGH",
            notification_type="general",
            link="https://example.com",
            expiry_date=timezone.now() + timedelta(days=7),
        )

        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )

        self.notification_user = NotificationUser.objects.create(
            user=self.user,
            notification=self.notification,
            seen=True,
            seen_at=timezone.now(),
        )

    def test_get_queryset(self):
        request = self.factory.get("/")
        request.user = Mock()

        queryset = self.admin.get_queryset(request)

        self.assertTrue(hasattr(queryset, "_prefetch_related_lookups"))
        self.assertIn("user__user", queryset._prefetch_related_lookups)

    def test_notification_info(self):
        result = self.admin.notification_info(self.notification)

        self.assertIn("Test Notification", result)
        self.assertIn("Test notification message", result)
        self.assertIn("🔗", result)
        self.assertIn("ID:", result)

    def test_priority_badge(self):
        test_cases = [
            ("LOW", "inline-flex"),
            ("NORMAL", "inline-flex"),
            ("HIGH", "inline-flex"),
            ("URGENT", "inline-flex"),
            ("CRITICAL", "inline-flex"),
        ]

        for priority, expected_css in test_cases:
            with self.subTest(priority=priority):
                self.notification.priority = priority
                result = self.admin.priority_badge(self.notification)

                self.assertIn(expected_css, result)
                self.assertTrue(len(result) > 0)

    def test_category_badge(self):
        test_cases = [
            ("SYSTEM", "⚙️"),
            ("ORDER", "📦"),
            ("PAYMENT", "💳"),
            ("SECURITY", "🔐"),
            ("PROMOTION", "🎉"),
        ]

        for category, expected_emoji in test_cases:
            with self.subTest(category=category):
                self.notification.category = category
                result = self.admin.category_badge(self.notification)

                self.assertIn(expected_emoji, result)
                self.assertIn("inline-flex", result)

    def test_status_display(self):
        result = self.admin.status_display(self.notification)
        self.assertIn("Active", result)

        self.notification.expiry_date = timezone.now() - timedelta(days=1)
        result = self.admin.status_display(self.notification)
        self.assertIn("Expired", result)

        self.notification.expiry_date = None
        result = self.admin.status_display(self.notification)
        self.assertIn("Active", result)

    def test_engagement_stats(self):
        result = self.admin.engagement_stats(self.notification)

        self.assertIn("👥", result)
        self.assertIn("👁️", result)
        self.assertIn("👓", result)
        self.assertIn("%", result)

    def test_timing_info(self):
        result = self.admin.timing_info(self.notification)

        self.assertIn("ago", result)
        self.assertTrue(len(result) > 0)
        import re

        self.assertTrue(re.search(r"\d{2}-\d{2}", result))
        self.assertTrue(
            "left" in result or "No expiry" in result or "Expired" in result
        )

    def test_notification_analytics(self):
        result = self.admin.notification_analytics(self.notification)

        self.assertIn("Age:", result)
        self.assertIn("Title Length:", result)
        self.assertIn("Message Length:", result)
        self.assertIn("Has Link:", result)
        self.assertIn("Has Type:", result)
        self.assertIn("Readability:", result)
        self.assertIn("chars", result)

    def test_engagement_summary(self):
        result = self.admin.engagement_summary(self.notification)

        self.assertIn("Total Recipients:", result)
        self.assertIn("Seen:", result)
        self.assertIn("Unseen:", result)
        self.assertIn("Engagement Rate:", result)
        self.assertIn("%", result)
        self.assertIn("Performance:", result)

    def test_timing_summary(self):
        result = self.admin.timing_summary(self.notification)

        self.assertIn("Created", result)
        if self.notification.expiry_date:
            self.assertIn("Expires", result)


class NotificationUserAdminTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = NotificationUserAdmin(NotificationUser, self.site)

        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )

        self.notification = Notification.objects.create(
            title="Test Notification",
            message="Test message",
            kind="INFO",
            category="SYSTEM",
            priority="HIGH",
            notification_type="general",
        )

        self.notification_user = NotificationUser.objects.create(
            user=self.user,
            notification=self.notification,
            seen=True,
            seen_at=timezone.now(),
        )

    def test_get_queryset(self):
        request = self.factory.get("/")
        request.user = Mock()

        queryset = self.admin.get_queryset(request)

        self.assertTrue(
            hasattr(queryset, "_prefetch_related_lookups")
            or hasattr(queryset, "query")
        )

    def test_user_info(self):
        result = self.admin.user_info(self.notification_user)

        self.assertIn("testuser", result)
        self.assertIn("test@example.com", result)

    def test_notification_info(self):
        result = self.admin.notification_info(self.notification_user)

        self.assertIn("Test Notification", result)
        self.assertTrue(len(result) > 0)
        self.assertIn("#", result)

    def test_seen_status(self):
        result = self.admin.seen_status(self.notification_user)
        self.assertIn("Seen", result)

        self.notification_user.seen = False
        self.notification_user.seen_at = None
        result = self.admin.seen_status(self.notification_user)
        self.assertIn("Unseen", result)

    def test_priority_indicator(self):
        result = self.admin.priority_indicator(self.notification_user)

        self.assertIn("⚠️", result)
        self.assertIn("text-orange-600", result)
        self.assertTrue(len(result) > 0)

    def test_timing_display(self):
        result = self.admin.timing_display(self.notification_user)

        self.assertIn("ago", result)
        self.assertTrue(len(result) > 0)
        import re

        self.assertTrue(re.search(r"\d{2}-\d{2}", result))
        self.assertTrue("Active" in result or "Expired" in result)

    def test_user_notification_analytics(self):
        result = self.admin.user_notification_analytics(self.notification_user)

        self.assertIn("Age:", result)
        self.assertIn("Response Time:", result)
        self.assertIn("Notification Status:", result)
        self.assertIn("Priority Level:", result)
        self.assertIn("Category:", result)
        self.assertIn("Engagement:", result)


class NotificationAdminIntegrationTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.notification_admin = NotificationAdmin(Notification, self.site)
        self.notification_user_admin = NotificationUserAdmin(
            NotificationUser, self.site
        )

        self.user = User.objects.create_user(
            username="adminuser",
            email="admin@example.com",
            password="adminpass123",
        )

        self.notifications = []
        priorities = ["LOW", "NORMAL", "HIGH", "URGENT", "CRITICAL"]
        categories = ["SYSTEM", "ORDER", "PAYMENT", "SECURITY", "PROMOTION"]

        for i, (priority, category) in enumerate(zip(priorities, categories)):
            notification = Notification.objects.create(
                title=f"Test Notification {i + 1}",
                message=f"Test message {i + 1}",
                kind="INFO",
                category=category,
                priority=priority,
                notification_type="general",
            )
            self.notifications.append(notification)

            NotificationUser.objects.create(
                user=self.user,
                notification=notification,
                seen=i % 2 == 0,
            )

    def test_admin_display_methods_integration(self):
        request = self.factory.get("/")
        request.user = Mock()

        for notification in self.notifications:
            notification_info = self.notification_admin.notification_info(
                notification
            )
            priority_badge = self.notification_admin.priority_badge(
                notification
            )
            category_badge = self.notification_admin.category_badge(
                notification
            )
            status_display = self.notification_admin.status_display(
                notification
            )

            self.assertIsInstance(notification_info, str)
            self.assertIsInstance(priority_badge, str)
            self.assertIsInstance(category_badge, str)
            self.assertIsInstance(status_display, str)

            self.assertIn(notification.title, notification_info)
            self.assertIn("inline-flex", priority_badge)
            self.assertIn("inline-flex", category_badge)

    def test_filter_functionality(self):
        request = self.factory.get("/")
        request.user = Mock()

        status_filter = NotificationStatusFilter(
            request, {}, Notification, self.notification_admin
        )

        filter_tests = [
            ("urgent", lambda n: n.priority in ["URGENT", "CRITICAL"]),
            ("system", lambda n: n.category == "SYSTEM"),
            ("order", lambda n: n.category == "ORDER"),
            ("payment", lambda n: n.category == "PAYMENT"),
        ]

        for filter_value, condition_func in filter_tests:
            status_filter.used_parameters = {
                "notification_status": filter_value
            }
            filtered_queryset = status_filter.queryset(
                request, Notification.objects.all()
            )

            for notification in filtered_queryset:
                self.assertTrue(condition_func(notification))

    def test_queryset_optimization(self):
        request = self.factory.get("/")
        request.user = Mock()

        notification_queryset = self.notification_admin.get_queryset(request)
        self.assertTrue(
            hasattr(notification_queryset, "_prefetch_related_lookups")
        )

        user_queryset = self.notification_user_admin.get_queryset(request)
        self.assertTrue(hasattr(user_queryset, "query"))
