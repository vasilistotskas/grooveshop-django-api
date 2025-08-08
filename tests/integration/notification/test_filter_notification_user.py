from django.contrib.auth import get_user_model
from django.urls import reverse
from datetime import timedelta
from django.utils import timezone
from rest_framework.test import APITestCase

from notification.factories.notification import NotificationFactory
from notification.factories.user import NotificationUserFactory
from notification.models.user import NotificationUser
from notification.enum import (
    NotificationKindEnum,
    NotificationCategoryEnum,
    NotificationPriorityEnum,
)
from user.factories.account import UserAccountFactory

User = get_user_model()


class NotificationUserFilterTest(APITestCase):
    def setUp(self):
        NotificationUser.objects.all().delete()

        self.user1 = UserAccountFactory(
            email="user1@example.com",
            first_name="John",
            last_name="Doe",
            is_active=True,
            is_staff=False,
        )
        self.user2 = UserAccountFactory(
            email="user2@example.com",
            first_name="Jane",
            last_name="Smith",
            is_active=True,
            is_staff=True,
        )
        self.inactive_user = UserAccountFactory(
            email="inactive@example.com",
            first_name="Inactive",
            last_name="User",
            is_active=False,
            is_staff=False,
        )

        self.now = timezone.now()

        self.high_priority_notification = NotificationFactory(
            kind=NotificationKindEnum.ERROR,
            category=NotificationCategoryEnum.ORDER,
            priority=NotificationPriorityEnum.HIGH,
            notification_type="order_failed",
            link="https://example.com/order/123",
            expiry_date=self.now + timedelta(days=30),
        )
        self.high_priority_notification.created_at = self.now - timedelta(
            hours=1
        )
        self.high_priority_notification.save()

        self.normal_notification = NotificationFactory(
            kind=NotificationKindEnum.INFO,
            category=NotificationCategoryEnum.SYSTEM,
            priority=NotificationPriorityEnum.NORMAL,
            notification_type="system_update",
            link="https://example.com/system",
            expiry_date=self.now + timedelta(days=7),
        )
        self.normal_notification.created_at = self.now - timedelta(days=2)
        self.normal_notification.save()

        self.expired_notification = NotificationFactory(
            kind=NotificationKindEnum.WARNING,
            category=NotificationCategoryEnum.PAYMENT,
            priority=NotificationPriorityEnum.NORMAL,
            notification_type="payment_reminder",
            link="https://example.com/payment",
            expiry_date=self.now - timedelta(days=1),
        )
        self.expired_notification.created_at = self.now - timedelta(days=10)
        self.expired_notification.save()

        self.old_notification = NotificationFactory(
            kind=NotificationKindEnum.SUCCESS,
            category=NotificationCategoryEnum.ACCOUNT,
            priority=NotificationPriorityEnum.LOW,
            notification_type="account_verified",
            link="",
            expiry_date=self.now + timedelta(days=60),
        )
        self.old_notification.created_at = self.now - timedelta(days=30)
        self.old_notification.save()

        self.seen_notification_user = NotificationUserFactory(
            user=self.user1,
            notification=self.high_priority_notification,
            seen=True,
            seen_at=self.now - timedelta(minutes=30),
        )

        self.unseen_notification_user = NotificationUserFactory(
            user=self.user1,
            notification=self.normal_notification,
            seen=False,
            seen_at=None,
        )

        self.recent_seen_notification_user = NotificationUserFactory(
            user=self.user2,
            notification=self.normal_notification,
            seen=True,
            seen_at=self.now - timedelta(hours=2),
        )

        self.old_unseen_notification_user = NotificationUserFactory(
            user=self.user2,
            notification=self.old_notification,
            seen=False,
            seen_at=None,
        )

        self.expired_notification_user = NotificationUserFactory(
            user=self.inactive_user,
            notification=self.expired_notification,
            seen=False,
            seen_at=None,
        )

    def test_basic_filters(self):
        url = reverse("notification-user-list")

        response = self.client.get(url, {"user": self.user1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.seen_notification_user.id, result_ids)
        self.assertIn(self.unseen_notification_user.id, result_ids)

        response = self.client.get(
            url, {"notification": self.normal_notification.id}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.unseen_notification_user.id, result_ids)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)

        response = self.client.get(url, {"seen": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.seen_notification_user.id, result_ids)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)

        response = self.client.get(url, {"seen": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.unseen_notification_user.id, result_ids)
        self.assertIn(self.old_unseen_notification_user.id, result_ids)
        self.assertIn(self.expired_notification_user.id, result_ids)

    def test_user_relationship_filters(self):
        url = reverse("notification-user-list")

        response = self.client.get(url, {"user__email": "user1"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.seen_notification_user.id, result_ids)
        self.assertIn(self.unseen_notification_user.id, result_ids)

        response = self.client.get(url, {"user__first_name": "jane"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)
        self.assertIn(self.old_unseen_notification_user.id, result_ids)

        response = self.client.get(url, {"user__is_active": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.expired_notification_user.id, result_ids)

        response = self.client.get(url, {"user__is_staff": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)
        self.assertIn(self.old_unseen_notification_user.id, result_ids)

    def test_seen_timestamp_filters(self):
        url = reverse("notification-user-list")

        seen_after_date = self.now - timedelta(hours=1)
        response = self.client.get(
            url, {"seen_after": seen_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.seen_notification_user.id, result_ids)

        seen_before_date = self.now - timedelta(hours=1)
        response = self.client.get(
            url, {"seen_before": seen_before_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)

        response = self.client.get(url, {"has_seen_at": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.seen_notification_user.id, result_ids)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)

        response = self.client.get(url, {"has_seen_at": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.unseen_notification_user.id, result_ids)
        self.assertIn(self.old_unseen_notification_user.id, result_ids)
        self.assertIn(self.expired_notification_user.id, result_ids)

    def test_notification_relationship_filters(self):
        url = reverse("notification-user-list")

        response = self.client.get(
            url, {"notification__kind": NotificationKindEnum.ERROR}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.seen_notification_user.id, result_ids)

        response = self.client.get(
            url, {"notification__category": NotificationCategoryEnum.SYSTEM}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.unseen_notification_user.id, result_ids)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)

        response = self.client.get(
            url, {"notification__priority": NotificationPriorityEnum.HIGH}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.seen_notification_user.id, result_ids)

        response = self.client.get(url, {"notification__type": "order"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.seen_notification_user.id, result_ids)

        response = self.client.get(
            url, {"notification__link": "example.com/system"}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.unseen_notification_user.id, result_ids)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)

    def test_notification_expiry_filters(self):
        url = reverse("notification-user-list")

        response = self.client.get(url, {"notification__is_expired": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.expired_notification_user.id, result_ids)

        response = self.client.get(url, {"notification__is_expired": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertNotIn(self.expired_notification_user.id, result_ids)

        expires_after_date = self.now + timedelta(days=10)
        response = self.client.get(
            url, {"notification__expires_after": expires_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.seen_notification_user.id, result_ids)
        self.assertIn(self.old_unseen_notification_user.id, result_ids)

        expires_before_date = self.now + timedelta(days=10)
        response = self.client.get(
            url,
            {"notification__expires_before": expires_before_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.unseen_notification_user.id, result_ids)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)
        self.assertIn(self.expired_notification_user.id, result_ids)

    def test_special_filters(self):
        url = reverse("notification-user-list")

        response = self.client.get(url, {"unseen_only": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.unseen_notification_user.id, result_ids)
        self.assertIn(self.old_unseen_notification_user.id, result_ids)
        self.assertIn(self.expired_notification_user.id, result_ids)

        response = self.client.get(url, {"seen_only": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.seen_notification_user.id, result_ids)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)

        response = self.client.get(url, {"recent_notifications": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.seen_notification_user.id, result_ids)
        self.assertIn(self.unseen_notification_user.id, result_ids)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)

        response = self.client.get(url, {"high_priority": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.seen_notification_user.id, result_ids)

    def test_bulk_filters(self):
        url = reverse("notification-user-list")

        user_ids = f"{self.user1.id},{self.user2.id}"
        response = self.client.get(url, {"user_ids": user_ids})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertNotIn(self.expired_notification_user.id, result_ids)

        notification_ids = f"{self.high_priority_notification.id},{self.normal_notification.id}"
        response = self.client.get(url, {"notification_ids": notification_ids})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.seen_notification_user.id, result_ids)
        self.assertIn(self.unseen_notification_user.id, result_ids)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)

        response = self.client.get(url, {"user_ids": "invalid,ids"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 0)

    def test_uuid_filter(self):
        url = reverse("notification-user-list")

        response = self.client.get(
            url, {"uuid": str(self.seen_notification_user.uuid)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.seen_notification_user.id
        )

    def test_timestamp_filters(self):
        url = reverse("notification-user-list")

        created_after_date = self.now - timedelta(minutes=5)
        response = self.client.get(
            url, {"created_after": created_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 5)

        created_before_date = self.now + timedelta(minutes=5)
        response = self.client.get(
            url, {"created_before": created_before_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 5)

    def test_camel_case_filters(self):
        url = reverse("notification-user-list")

        response = self.client.get(
            url,
            {
                "user__email": "user1",
                "notification__kind": NotificationKindEnum.ERROR,
                "unseen_only": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 0)

        response = self.client.get(
            url,
            {
                "user__email": "user1",
                "notification__category": NotificationCategoryEnum.SYSTEM,
                "unseen_only": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.unseen_notification_user.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("notification-user-list")

        response = self.client.get(
            url,
            {
                "user__is_active": "true",
                "seen": "false",
                "notification__priority": NotificationPriorityEnum.HIGH,
                "ordering": "-created_at",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 0)

        response = self.client.get(
            url,
            {
                "user__is_staff": "true",
                "recent_notifications": "true",
                "ordering": "-notification__created_at",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.recent_seen_notification_user.id, result_ids)

        created_after_date = self.now - timedelta(days=5)
        expires_after_date = self.now + timedelta(days=5)
        response = self.client.get(
            url,
            {
                "created_after": created_after_date.isoformat(),
                "notification__expires_after": expires_after_date.isoformat(),
                "seen": "true",
                "ordering": "-seen_at",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(len(result_ids), 1)

    def test_filter_with_ordering(self):
        url = reverse("notification-user-list")

        response = self.client.get(
            url, {"seen": "true", "ordering": "-seen_at"}
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual(len(results), 2)

        self.assertEqual(results[0]["id"], self.seen_notification_user.id)
        self.assertEqual(
            results[1]["id"], self.recent_seen_notification_user.id
        )

        response = self.client.get(
            url,
            {"user": self.user1.id, "ordering": "-notification__created_at"},
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual(len(results), 2)

        self.assertEqual(results[0]["id"], self.seen_notification_user.id)
        self.assertEqual(results[1]["id"], self.unseen_notification_user.id)

    def tearDown(self):
        NotificationUser.objects.all().delete()
