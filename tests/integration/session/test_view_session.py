from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

from allauth.usersessions.models import UserSession
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


class SessionAPITestCase(APITestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="adminpassword",
        )
        self.regular_user = User.objects.create_user(
            username="user", email="user@example.com", password="userpassword"
        )

    @staticmethod
    def get_session_active_users_count_url():
        return reverse("session-active-users-count")

    @patch.dict(
        "os.environ",
        {"DEFAULT_CACHE_KEY_PREFIX": "redis", "DEFAULT_CACHE_VERSION": "1"},
    )
    @patch("django.utils.timezone.now")
    def test_active_users_count_admin_access(self, mock_now):
        fixed_now = datetime(2024, 12, 8, 12, 0, 0, tzinfo=UTC)
        mock_now.return_value = fixed_now
        one_hour_ago = fixed_now - timedelta(hours=1)

        self.client.force_authenticate(user=self.admin_user)

        with (
            patch("core.caches.cache_instance.keys") as mock_keys,
            patch("core.caches.cache_instance.get") as mock_get,
            patch(
                "allauth.usersessions.models.UserSession.objects.filter"
            ) as mock_filter,
        ):
            mock_keys.return_value = [
                "redis:1:django.contrib.sessions.cachesession1",
                "redis:1:django.contrib.sessions.cachesession2",
                "redis:1:django.contrib.sessions.cachesession3",
            ]

            mock_get.return_value = b"session_data"

            mock_queryset = MagicMock()
            mock_queryset.exists.side_effect = [True, False, True]
            mock_filter.return_value = mock_queryset

            with patch(
                "allauth.usersessions.models.UserSession.objects.get"
            ) as mock_get_session:
                session1 = Mock(spec=UserSession)
                session1.user_id = 1
                session3 = Mock(spec=UserSession)
                session3.user_id = 2

                def get_session_side_effect(session_key):
                    if session_key == "session1":
                        return session1
                    elif session_key == "session3":
                        return session3
                    else:
                        raise UserSession.DoesNotExist

                mock_get_session.side_effect = get_session_side_effect

                response = self.client.get(
                    self.get_session_active_users_count_url()
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.json(), {"activeUsers": 2})

                mock_keys.assert_called_once_with(
                    "django.contrib.sessions.cache*"
                )
                self.assertEqual(mock_get.call_count, 3)
                self.assertEqual(mock_filter.call_count, 3)
                mock_filter.assert_any_call(
                    session_key="session1", last_seen_at__gte=one_hour_ago
                )
                mock_filter.assert_any_call(
                    session_key="session2", last_seen_at__gte=one_hour_ago
                )
                mock_filter.assert_any_call(
                    session_key="session3", last_seen_at__gte=one_hour_ago
                )
                mock_get_session.assert_any_call(session_key="session1")
                mock_get_session.assert_any_call(session_key="session3")

    @patch("django.utils.timezone.now")
    def test_active_users_count_non_admin_access(self, mock_now):
        fixed_now = datetime(2024, 12, 8, 12, 0, 0, tzinfo=UTC)
        mock_now.return_value = fixed_now

        self.client.force_authenticate(user=self.regular_user)

        response = self.client.get(self.get_session_active_users_count_url())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("django.utils.timezone.now")
    def test_active_users_count_no_active_users(self, mock_now):
        fixed_now = datetime(2024, 12, 8, 12, 0, 0, tzinfo=UTC)
        mock_now.return_value = fixed_now

        self.client.force_authenticate(user=self.admin_user)

        with (
            patch("core.caches.cache_instance.keys") as mock_keys,
            patch("core.caches.cache_instance.get") as mock_get,
            patch(
                "allauth.usersessions.models.UserSession.objects.filter"
            ) as mock_filter,
        ):
            mock_keys.return_value = []

            response = self.client.get(
                self.get_session_active_users_count_url()
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.json(), {"activeUsers": 0})

            mock_keys.assert_called_once_with("django.contrib.sessions.cache*")
            mock_get.assert_not_called()
            mock_filter.assert_not_called()

    @patch.dict(
        "os.environ",
        {"DEFAULT_CACHE_KEY_PREFIX": "redis", "DEFAULT_CACHE_VERSION": "1"},
    )
    @patch("django.utils.timezone.now")
    def test_active_users_count_with_active_users(self, mock_now):
        fixed_now = datetime(2024, 12, 8, 12, 0, 0, tzinfo=UTC)
        mock_now.return_value = fixed_now
        one_hour_ago = fixed_now - timedelta(hours=1)

        self.client.force_authenticate(user=self.admin_user)

        with (
            patch("core.caches.cache_instance.keys") as mock_keys,
            patch("core.caches.cache_instance.get") as mock_get,
            patch(
                "allauth.usersessions.models.UserSession.objects.filter"
            ) as mock_filter,
        ):
            mock_keys.return_value = [
                "redis:1:django.contrib.sessions.cachesession1",
                "redis:1:django.contrib.sessions.cachesession2",
            ]

            mock_get.return_value = b"session_data"

            mock_queryset = MagicMock()
            mock_queryset.exists.side_effect = [True, True]
            mock_filter.return_value = mock_queryset

            with patch(
                "allauth.usersessions.models.UserSession.objects.get"
            ) as mock_get_session:
                session1 = Mock(spec=UserSession)
                session1.user_id = 1
                session2 = Mock(spec=UserSession)
                session2.user_id = 2

                def get_session_side_effect(session_key):
                    if session_key == "session1":
                        return session1
                    elif session_key == "session2":
                        return session2
                    else:
                        raise UserSession.DoesNotExist

                mock_get_session.side_effect = get_session_side_effect

                response = self.client.get(
                    self.get_session_active_users_count_url()
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.json(), {"activeUsers": 2})

                mock_keys.assert_called_once_with(
                    "django.contrib.sessions.cache*"
                )
                self.assertEqual(mock_get.call_count, 2)
                self.assertEqual(mock_filter.call_count, 2)
                mock_filter.assert_any_call(
                    session_key="session1", last_seen_at__gte=one_hour_ago
                )
                mock_filter.assert_any_call(
                    session_key="session2", last_seen_at__gte=one_hour_ago
                )
                mock_get_session.assert_any_call(session_key="session1")
                mock_get_session.assert_any_call(session_key="session2")

    @patch.dict(
        "os.environ",
        {"DEFAULT_CACHE_KEY_PREFIX": "redis", "DEFAULT_CACHE_VERSION": "1"},
    )
    @patch("django.utils.timezone.now")
    def test_active_users_count_with_mixed_sessions(self, mock_now):
        fixed_now = datetime(2024, 12, 8, 12, 0, 0, tzinfo=UTC)
        mock_now.return_value = fixed_now
        one_hour_ago = fixed_now - timedelta(hours=1)

        self.client.force_authenticate(user=self.admin_user)

        with (
            patch("core.caches.cache_instance.keys") as mock_keys,
            patch("core.caches.cache_instance.get") as mock_get,
            patch(
                "allauth.usersessions.models.UserSession.objects.filter"
            ) as mock_filter,
        ):
            mock_keys.return_value = [
                "redis:1:django.contrib.sessions.cachesession1",
                "redis:1:django.contrib.sessions.cachesession2",
                "redis:1:django.contrib.sessions.cachesession3",
            ]

            mock_get.return_value = b"session_data"

            mock_queryset = MagicMock()
            mock_queryset.exists.side_effect = [True, False, True]
            mock_filter.return_value = mock_queryset

            with patch(
                "allauth.usersessions.models.UserSession.objects.get"
            ) as mock_get_session:
                session1 = Mock(spec=UserSession)
                session1.user_id = 1
                session3 = Mock(spec=UserSession)
                session3.user_id = 2

                def get_session_side_effect(session_key):
                    if session_key == "session1":
                        return session1
                    elif session_key == "session3":
                        return session3
                    else:
                        raise UserSession.DoesNotExist

                mock_get_session.side_effect = get_session_side_effect

                response = self.client.get(
                    self.get_session_active_users_count_url()
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.json(), {"activeUsers": 2})

                mock_keys.assert_called_once_with(
                    "django.contrib.sessions.cache*"
                )
                self.assertEqual(mock_get.call_count, 3)
                self.assertEqual(mock_filter.call_count, 3)
                mock_filter.assert_any_call(
                    session_key="session1", last_seen_at__gte=one_hour_ago
                )
                mock_filter.assert_any_call(
                    session_key="session2", last_seen_at__gte=one_hour_ago
                )
                mock_filter.assert_any_call(
                    session_key="session3", last_seen_at__gte=one_hour_ago
                )
                mock_get_session.assert_any_call(session_key="session1")
                mock_get_session.assert_any_call(session_key="session3")

    @patch.dict(
        "os.environ",
        {"DEFAULT_CACHE_KEY_PREFIX": "redis", "DEFAULT_CACHE_VERSION": "1"},
    )
    @patch("django.utils.timezone.now")
    def test_active_users_count_with_invalid_keys(self, mock_now):
        fixed_now = datetime(2024, 12, 8, 12, 0, 0, tzinfo=UTC)
        mock_now.return_value = fixed_now
        one_hour_ago = fixed_now - timedelta(hours=1)

        self.client.force_authenticate(user=self.admin_user)

        with (
            patch("core.caches.cache_instance.keys") as mock_keys,
            patch("core.caches.cache_instance.get") as mock_get,
            patch(
                "allauth.usersessions.models.UserSession.objects.filter"
            ) as mock_filter,
        ):
            mock_keys.return_value = [
                "redis:1:django.contrib.sessions.cachesession1",
                "invalidprefix:django.contrib.sessions.cachesession2",
                "redis:1:django.contrib.sessions.cachesession3",
            ]

            mock_get.side_effect = [b"session_data1", b"session_data3"]

            mock_queryset = MagicMock()
            mock_queryset.exists.side_effect = [True, True]
            mock_filter.return_value = mock_queryset

            with patch(
                "allauth.usersessions.models.UserSession.objects.get"
            ) as mock_get_session:
                session1 = Mock(spec=UserSession)
                session1.user_id = 1
                session3 = Mock(spec=UserSession)
                session3.user_id = 2

                def get_session_side_effect(session_key):
                    if session_key == "session1":
                        return session1
                    elif session_key == "session3":
                        return session3
                    else:
                        raise UserSession.DoesNotExist

                mock_get_session.side_effect = get_session_side_effect

                response = self.client.get(
                    self.get_session_active_users_count_url()
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.json(), {"activeUsers": 2})

                mock_keys.assert_called_once_with(
                    "django.contrib.sessions.cache*"
                )
                self.assertEqual(mock_get.call_count, 2)
                self.assertEqual(mock_filter.call_count, 2)
                mock_filter.assert_any_call(
                    session_key="session1", last_seen_at__gte=one_hour_ago
                )
                mock_filter.assert_any_call(
                    session_key="session3", last_seen_at__gte=one_hour_ago
                )
                mock_get_session.assert_any_call(session_key="session1")
                mock_get_session.assert_any_call(session_key="session3")

    @patch("django.utils.timezone.now")
    def test_active_users_count_cache_failure(self, mock_now):
        fixed_now = datetime(2024, 12, 8, 12, 0, 0, tzinfo=UTC)
        mock_now.return_value = fixed_now

        self.client.force_authenticate(user=self.admin_user)

        with (
            patch("core.caches.cache_instance.keys") as mock_keys,
            patch("core.caches.cache_instance.get") as mock_get,
            patch(
                "allauth.usersessions.models.UserSession.objects.filter"
            ) as mock_filter,
        ):
            mock_keys.side_effect = Exception("Cache connection failed")

            response = self.client.get(
                self.get_session_active_users_count_url()
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.json(), {"activeUsers": 0})

            mock_keys.assert_called_once_with("django.contrib.sessions.cache*")
            mock_get.assert_not_called()
            mock_filter.assert_not_called()

    @patch.dict(
        "os.environ",
        {"DEFAULT_CACHE_KEY_PREFIX": "redis", "DEFAULT_CACHE_VERSION": "1"},
    )
    @patch("django.utils.timezone.now")
    def test_active_users_count_duplicate_users(self, mock_now):
        fixed_now = datetime(2024, 12, 8, 12, 0, 0, tzinfo=UTC)
        mock_now.return_value = fixed_now
        one_hour_ago = fixed_now - timedelta(hours=1)

        self.client.force_authenticate(user=self.admin_user)

        with (
            patch("core.caches.cache_instance.keys") as mock_keys,
            patch("core.caches.cache_instance.get") as mock_get,
            patch(
                "allauth.usersessions.models.UserSession.objects.filter"
            ) as mock_filter,
        ):
            mock_keys.return_value = [
                "redis:1:django.contrib.sessions.cachesession1",
                "redis:1:django.contrib.sessions.cachesession2",
            ]

            mock_get.return_value = b"session_data"

            mock_queryset = MagicMock()
            mock_queryset.exists.side_effect = [True, True]
            mock_filter.return_value = mock_queryset

            with patch(
                "allauth.usersessions.models.UserSession.objects.get"
            ) as mock_get_session:
                session1 = Mock(spec=UserSession)
                session1.user_id = 1
                session2 = Mock(spec=UserSession)
                session2.user_id = 1

                def get_session_side_effect(session_key):
                    if session_key == "session1":
                        return session1
                    elif session_key == "session2":
                        return session2
                    else:
                        raise UserSession.DoesNotExist

                mock_get_session.side_effect = get_session_side_effect

                response = self.client.get(
                    self.get_session_active_users_count_url()
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.json(), {"activeUsers": 1})

                mock_keys.assert_called_once_with(
                    "django.contrib.sessions.cache*"
                )
                self.assertEqual(mock_get.call_count, 2)
                self.assertEqual(mock_filter.call_count, 2)
                mock_filter.assert_any_call(
                    session_key="session1", last_seen_at__gte=one_hour_ago
                )
                mock_filter.assert_any_call(
                    session_key="session2", last_seen_at__gte=one_hour_ago
                )
                mock_get_session.assert_any_call(session_key="session1")
                mock_get_session.assert_any_call(session_key="session2")
