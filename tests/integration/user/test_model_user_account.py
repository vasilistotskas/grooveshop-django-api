from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import RequestFactory
from django.test import TestCase
from django.utils import timezone

from core import caches
from core.caches import cache_instance

User = get_user_model()


class UserAccountModelTest(TestCase):
    factory: RequestFactory = None
    user_data: dict = None

    def setUp(self):
        self.factory = RequestFactory()
        self.user_data = {
            "email": "testuser@example.com",
            "password": "testpassword",
            "first_name": "John",
            "last_name": "Doe",
        }

    def test_create_user(self):
        user = User.objects.create_user(**self.user_data)
        self.assertEqual(user.email, self.user_data["email"])
        self.assertTrue(user.check_password(self.user_data["password"]))
        self.assertEqual(user.first_name, self.user_data["first_name"])
        self.assertEqual(user.last_name, self.user_data["last_name"])
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_superuser(self):
        user = User.objects.create_superuser(**self.user_data)
        self.assertEqual(user.email, self.user_data["email"])
        self.assertTrue(user.check_password(self.user_data["password"]))
        self.assertEqual(user.first_name, self.user_data["first_name"])
        self.assertEqual(user.last_name, self.user_data["last_name"])
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_remove_all_sessions(self):
        # Create a user
        user = User.objects.create_user(**self.user_data)

        # Create the first session for the user
        session1 = Session.objects.create(
            session_key="testsession1",
            expire_date=timezone.now() + timedelta(days=1),
            session_data=f'auth_user_id": "{user.pk}"',
        )

        # Create the second session for the user with a different session key
        session2 = Session.objects.create(
            session_key="testsession2",
            expire_date=timezone.now() + timedelta(days=1),
            session_data=f'auth_user_id": "{user.pk}"',
        )

        # Create a mock request object for the first session
        request1 = self.factory.get("/")
        request1.user = user
        request1.session = session1

        # Create a mock request object for the second session
        request2 = self.factory.get("/")
        request2.user = user
        request2.session = session2

        # Assert that the sessions for the user exist
        self.assertTrue(Session.objects.filter(session_key="testsession1").exists())
        self.assertTrue(Session.objects.filter(session_key="testsession2").exists())

        # Instantiate the UserAccount model and call the remove_all_sessions method
        user_account = user
        user_account.remove_all_sessions(request1)

        # Assert that all sessions for the user are deleted
        self.assertFalse(Session.objects.filter(session_key="testsession1").exists())
        self.assertFalse(Session.objects.filter(session_key="testsession2").exists())

        # Assert that the cache for the current user is empty
        user_cache_key = caches.USER_AUTHENTICATED + "_" + str(user.id)
        self.assertIsNone(cache_instance.get(user_cache_key))

    def tearDown(self) -> None:
        super().tearDown()
        cache_instance.clear()
