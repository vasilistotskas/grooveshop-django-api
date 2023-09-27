from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.test import TestCase

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

    def tearDown(self) -> None:
        super().tearDown()
        cache_instance.clear()
