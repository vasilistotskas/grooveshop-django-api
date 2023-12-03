from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core import caches
from core.caches import cache_instance
from core.caches import USER_AUTHENTICATED

User = get_user_model()


class SessionAPITestCase(TestCase):
    def setUp(self):
        cache_instance.clear()
        self.client = APIClient()

    def tearDown(self):
        self.client.logout()
        cache_instance.clear()

    @staticmethod
    def get_session_active_users_count_url():
        return reverse("session-active-users-count")

    def test_active_users_count(self):
        cache_data_1 = {
            "last_activity": timezone.now(),
            "user": 1,
            "referer": "http://example.com",
            "session_key": "session_key_1",
            "cart_id": "cart_id_1",
        }
        user_1_key = f"{USER_AUTHENTICATED}1:session_key_1"
        cache_instance.set(user_1_key, cache_data_1, caches.ONE_HOUR)

        user_2_key = f"{USER_AUTHENTICATED}2:session_key_2"
        cache_data_2 = {
            "last_activity": timezone.now() - timedelta(minutes=20),
            "user": 2,
            "referer": "http://example.com",
            "session_key": "session_key_2",
            "cart_id": "cart_id_2",
        }
        cache_instance.set(user_2_key, cache_data_2, caches.ONE_HOUR)

        user_3_key = f"{USER_AUTHENTICATED}3:session_key_3"
        cache_data_3 = {
            "last_activity": timezone.now(),
            "user": 3,
            "referer": "http://example.com",
            "session_key": "session_key_3",
            "cart_id": "cart_id_3",
        }
        cache_instance.set(user_3_key, cache_data_3, caches.ONE_HOUR)

        response = self.client.get(self.get_session_active_users_count_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"active_users": 2})
