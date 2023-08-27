import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core import caches
from core.caches import cache_instance

User = get_user_model()


class ActiveUserViewSetTest(APITestCase):
    user: User = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )

    def test_active_users_count(self):
        # Delete all users, sessions and cache data
        User.objects.all().delete()
        cache_instance.clear()

        # Delete all sessions
        self.client.session.delete()

        # Set up additional users with varying last_activity timestamps
        user1 = User.objects.create_user(
            email="user1@example.com", password="testpassword"
        )
        user2 = User.objects.create_user(
            email="user2@example.com", password="testpassword"
        )

        # Json User
        json_user_1 = json.dumps(
            {"id": user1.id, "email": user1.email},
            cls=DjangoJSONEncoder,
        )
        json_user_2 = json.dumps(
            {"id": user2.id, "email": user2.email},
            cls=DjangoJSONEncoder,
        )

        # Get the current time and the time 20 minutes ago
        now = timezone.now()
        ten_minutes_ago = now - timedelta(minutes=20)

        # Set the last_activity for user1 to 20 minutes ago
        user1_cache_key = caches.USER_AUTHENTICATED + f"_{user1.id}"
        user1_cache_data = {
            "last_activity": ten_minutes_ago,
            "user": json_user_1,
            "cart_id": None,
            "pre_log_in_cart_id": None,
            "referer": None,
            "session_key": "user1_session_key",
        }
        cache_instance.set(user1_cache_key, user1_cache_data, caches.ONE_HOUR)

        # Set the last_activity for user2 to the current time
        user2_cache_key = caches.USER_AUTHENTICATED + f"_{user2.id}"
        user2_cache_data = {
            "last_activity": now,
            "user": json_user_2,
            "cart_id": None,
            "pre_log_in_cart_id": None,
            "referer": None,
            "session_key": "user2_session_key",
        }
        cache_instance.set(user2_cache_key, user2_cache_data, caches.ONE_HOUR)

        # Make a GET request to the active_users_count endpoint
        url = reverse("active-user-active-users-count")
        response = self.client.get(url)

        # Check the response status code and data
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["active_users"], 1
        )  # Only user2 should be active, user1's last_activity is too old

    def tearDown(self) -> None:
        super().tearDown()
        self.user.delete()
