from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.testing import TestURLFixerMixin
from notification.enum import (
    NotificationKindEnum,
)
from notification.factories.notification import NotificationFactory
from notification.factories.user import NotificationUserFactory
from notification.models.user import NotificationUser
from notification.serializers.user import (
    NotificationUserDetailSerializer,
    NotificationUserSerializer,
)
from user.factories.account import UserAccountFactory

User = get_user_model()


class NotificationUserViewSetTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory()
        cls.other_user = UserAccountFactory()
        cls.notification = NotificationFactory()
        cls.other_notification = NotificationFactory()

    def setUp(self):
        self.list_url = reverse("notification-user-list")

    def test_list_uses_correct_serializer(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        if response.data["results"]:
            serializer = NotificationUserSerializer(
                instance=response.data["results"][0]
            )
            expected_fields = set(serializer.data.keys())
            actual_fields = set(response.data["results"][0].keys())
            self.assertEqual(expected_fields, actual_fields)

    def test_retrieve_uses_correct_serializer(self):
        notification_user = NotificationUserFactory(
            user=self.user, notification=self.notification
        )
        detail_url = reverse(
            "notification-user-detail", args=[notification_user.id]
        )
        response = self.client.get(detail_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        serializer = NotificationUserDetailSerializer(
            instance=notification_user
        )
        expected_fields = set(serializer.data.keys())
        actual_fields = set(response.data.keys())
        self.assertEqual(expected_fields, actual_fields)

    def test_create_uses_correct_serializer(self):
        data = {
            "user": self.user.id,
            "notification": self.notification.id,
            "seen": False,
        }
        response = self.client.post(self.list_url, data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        created_obj = NotificationUser.objects.get(id=response.data["id"])
        serializer = NotificationUserDetailSerializer(instance=created_obj)
        expected_fields = set(serializer.data.keys())
        actual_fields = set(response.data.keys())
        self.assertEqual(expected_fields, actual_fields)

    def test_update_uses_correct_serializer(self):
        notification_user = NotificationUserFactory(
            user=self.user, notification=self.notification
        )
        detail_url = reverse(
            "notification-user-detail", args=[notification_user.id]
        )
        data = {
            "user": self.user.id,
            "notification": self.notification.id,
            "seen": True,
        }
        response = self.client.put(detail_url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["seen"], True)

    def test_partial_update_uses_correct_serializer(self):
        notification_user = NotificationUserFactory(
            user=self.user, notification=self.notification, seen=False
        )
        detail_url = reverse(
            "notification-user-detail", args=[notification_user.id]
        )
        data = {"seen": True}
        response = self.client.patch(detail_url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["seen"], True)

    def test_create_notification_user(self):
        initial_count = NotificationUser.objects.count()
        data = {
            "user": self.user.id,
            "notification": self.notification.id,
            "seen": False,
        }
        response = self.client.post(self.list_url, data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(NotificationUser.objects.count(), initial_count + 1)
        self.assertTrue(
            NotificationUser.objects.filter(
                user=self.user, notification=self.notification
            ).exists()
        )

    def test_create_duplicate_notification_user_fails(self):
        NotificationUserFactory(user=self.user, notification=self.notification)

        data = {
            "user": self.user.id,
            "notification": self.notification.id,
            "seen": False,
        }
        response = self.client.post(self.list_url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_notification_users(self):
        NotificationUserFactory(user=self.user, notification=self.notification)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)
        self.assertGreaterEqual(len(response.data["results"]), 1)

    def test_retrieve_notification_user(self):
        notification_user = NotificationUserFactory(
            user=self.user, notification=self.notification
        )
        detail_url = reverse(
            "notification-user-detail", args=[notification_user.id]
        )

        response = self.client.get(detail_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], notification_user.id)

    def test_update_notification_user(self):
        notification_user = NotificationUserFactory(
            user=self.user, notification=self.notification, seen=False
        )
        detail_url = reverse(
            "notification-user-detail", args=[notification_user.id]
        )

        data = {
            "user": self.user.id,
            "notification": self.notification.id,
            "seen": True,
        }
        response = self.client.put(detail_url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["seen"], True)

    def test_delete_notification_user(self):
        notification_user = NotificationUserFactory(
            user=self.user, notification=self.notification
        )
        detail_url = reverse(
            "notification-user-detail", args=[notification_user.id]
        )

        response = self.client.delete(detail_url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            NotificationUser.objects.filter(id=notification_user.id).exists()
        )

    def test_retrieve_nonexistent_notification_user(self):
        detail_url = reverse("notification-user-detail", args=[999999])
        response = self.client.get(detail_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_filter_by_seen(self):
        seen_notification = NotificationUserFactory(
            user=self.user, notification=self.notification, seen=True
        )
        unseen_notification = NotificationUserFactory(
            user=self.user, notification=self.other_notification, seen=False
        )

        response = self.client.get(self.list_url, {"seen": "true"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        seen_ids = [item["id"] for item in response.data["results"]]
        self.assertIn(seen_notification.id, seen_ids)
        self.assertNotIn(unseen_notification.id, seen_ids)

        response = self.client.get(self.list_url, {"seen": "false"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        unseen_ids = [item["id"] for item in response.data["results"]]
        self.assertIn(unseen_notification.id, unseen_ids)
        self.assertNotIn(seen_notification.id, unseen_ids)

    def test_filter_by_user(self):
        NotificationUserFactory(user=self.user, notification=self.notification)
        NotificationUserFactory(
            user=self.other_user, notification=self.other_notification
        )

        response = self.client.get(self.list_url, {"user": self.user.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user_ids = [item["user"] for item in response.data["results"]]
        self.assertIn(self.user.id, user_ids)
        self.assertNotIn(self.other_user.id, user_ids)

    def test_filter_by_notification(self):
        NotificationUserFactory(user=self.user, notification=self.notification)
        NotificationUserFactory(
            user=self.user, notification=self.other_notification
        )

        response = self.client.get(
            self.list_url, {"notification": self.notification.id}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notification_ids = [
            item["notification"] for item in response.data["results"]
        ]
        self.assertIn(self.notification.id, notification_ids)
        self.assertNotIn(self.other_notification.id, notification_ids)

    def test_filter_by_notification_kind(self):
        success_notification = NotificationFactory(
            kind=NotificationKindEnum.SUCCESS
        )
        warning_notification = NotificationFactory(
            kind=NotificationKindEnum.WARNING
        )

        success_nu = NotificationUserFactory(
            user=self.user, notification=success_notification
        )
        warning_nu = NotificationUserFactory(
            user=self.user, notification=warning_notification
        )

        response = self.client.get(
            self.list_url, {"notification_kind": NotificationKindEnum.SUCCESS}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_ids = [item["id"] for item in response.data["results"]]
        self.assertIn(success_nu.id, result_ids)
        self.assertNotIn(warning_nu.id, result_ids)

    def test_filter_by_date_range(self):
        old_date = timezone.now() - timedelta(days=10)
        recent_date = timezone.now() - timedelta(days=1)

        old_notification = NotificationUserFactory(
            user=self.user, notification=self.notification
        )
        old_notification.created_at = old_date
        old_notification.save()

        recent_notification = NotificationUserFactory(
            user=self.user, notification=self.other_notification
        )
        recent_notification.created_at = recent_date
        recent_notification.save()

        filter_date = timezone.now() - timedelta(days=5)
        response = self.client.get(
            self.list_url, {"created_after": filter_date.isoformat()}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_ids = [item["id"] for item in response.data["results"]]
        self.assertIn(recent_notification.id, result_ids)
        self.assertNotIn(old_notification.id, result_ids)

    def test_filter_has_seen_at(self):
        seen_notification = NotificationUserFactory(
            user=self.user,
            notification=self.notification,
            seen=True,
            seen_at=timezone.now(),
        )
        unseen_notification = NotificationUserFactory(
            user=self.user,
            notification=self.other_notification,
            seen=False,
            seen_at=None,
        )

        response = self.client.get(self.list_url, {"has_seen_at": "true"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_ids = [item["id"] for item in response.data["results"]]
        self.assertIn(seen_notification.id, result_ids)
        self.assertNotIn(unseen_notification.id, result_ids)

        response = self.client.get(self.list_url, {"has_seen_at": "false"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_ids = [item["id"] for item in response.data["results"]]
        self.assertIn(unseen_notification.id, result_ids)
        self.assertNotIn(seen_notification.id, result_ids)

    def test_ordering_by_created_at_desc(self):
        NotificationUserFactory(user=self.user, notification=self.notification)
        NotificationUserFactory(
            user=self.user, notification=self.other_notification
        )

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        if len(response.data["results"]) >= 2:
            first_item = response.data["results"][0]
            second_item = response.data["results"][1]
            self.assertGreaterEqual(
                first_item["created_at"], second_item["created_at"]
            )

    def test_ordering_by_seen(self):
        NotificationUserFactory(
            user=self.user, notification=self.notification, seen=True
        )
        NotificationUserFactory(
            user=self.user, notification=self.other_notification, seen=False
        )

        response = self.client.get(self.list_url, {"ordering": "seen"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data["results"]), 2)

    def test_search_by_user_name(self):
        user_with_unique_name = UserAccountFactory(first_name="UniqueFirstName")
        NotificationUserFactory(
            user=user_with_unique_name, notification=self.notification
        )

        response = self.client.get(self.list_url, {"search": "UniqueFirstName"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_by_notification_title(self):
        unique_notification = NotificationFactory()
        unique_notification.title = "UniqueNotificationTitle"
        unique_notification.save()

        NotificationUserFactory(
            user=self.user, notification=unique_notification
        )

        response = self.client.get(
            self.list_url, {"search": "UniqueNotificationTitle"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unseen_count_with_unseen_notifications(self):
        self.client.force_authenticate(user=self.user)

        NotificationUserFactory(
            user=self.user, notification=self.notification, seen=False
        )
        NotificationUserFactory(
            user=self.user, notification=self.other_notification, seen=True
        )

        url = reverse("notification-user-unseen-count")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

    def test_unseen_count_no_unseen_notifications(self):
        self.client.force_authenticate(user=self.user)

        NotificationUserFactory(
            user=self.user, notification=self.notification, seen=True
        )

        url = reverse("notification-user-unseen-count")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertIn("info", response.data)

    def test_unseen_count_unauthenticated(self):
        url = reverse("notification-user-unseen-count")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_mark_all_as_seen(self):
        self.client.force_authenticate(user=self.user)

        NotificationUserFactory(
            user=self.user, notification=self.notification, seen=False
        )
        NotificationUserFactory(
            user=self.user, notification=self.other_notification, seen=False
        )

        url = reverse("notification-user-mark-all-as-seen")
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["success"], True)

        self.assertEqual(
            NotificationUser.objects.filter(user=self.user, seen=False).count(),
            0,
        )

    def test_mark_all_as_unseen(self):
        self.client.force_authenticate(user=self.user)

        NotificationUserFactory(
            user=self.user, notification=self.notification, seen=True
        )
        NotificationUserFactory(
            user=self.user, notification=self.other_notification, seen=True
        )

        url = reverse("notification-user-mark-all-as-unseen")
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["success"], True)

        self.assertEqual(
            NotificationUser.objects.filter(user=self.user, seen=True).count(),
            0,
        )

    def test_mark_as_seen_specific(self):
        self.client.force_authenticate(user=self.user)

        nu1 = NotificationUserFactory(
            user=self.user, notification=self.notification, seen=False
        )
        nu2 = NotificationUserFactory(
            user=self.user, notification=self.other_notification, seen=False
        )

        url = reverse("notification-user-mark-as-seen")
        data = {"notification_user_ids": [nu1.id]}
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["success"], True)

        nu1.refresh_from_db()
        nu2.refresh_from_db()
        self.assertTrue(nu1.seen)
        self.assertFalse(nu2.seen)

    def test_mark_as_unseen_specific(self):
        self.client.force_authenticate(user=self.user)

        nu1 = NotificationUserFactory(
            user=self.user, notification=self.notification, seen=True
        )
        nu2 = NotificationUserFactory(
            user=self.user, notification=self.other_notification, seen=True
        )

        url = reverse("notification-user-mark-as-unseen")
        data = {"notification_user_ids": [nu1.id]}
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["success"], True)

        nu1.refresh_from_db()
        nu2.refresh_from_db()
        self.assertFalse(nu1.seen)
        self.assertTrue(nu2.seen)

    def test_mark_as_seen_empty_list(self):
        self.client.force_authenticate(user=self.user)

        url = reverse("notification-user-mark-as-seen")
        data = {"notification_user_ids": []}
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validation_errors(self):
        response = self.client.post(self.list_url, {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        data = {
            "user": self.user.id,
            "notification": 999999,
            "seen": False,
        }
        response = self.client.post(self.list_url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
