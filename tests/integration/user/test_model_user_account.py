from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.test import TestCase

from core.caches import cache_instance
from core.generators import UserNameGenerator

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
            "phone": "+1234567890",
            "city": "Sample City",
            "zipcode": "12345",
        }
        self.user_name_generator = UserNameGenerator()

    def test_create_user_with_generated_username(self):
        generated_username = self.user_name_generator.generate_username(
            self.user_data["email"]
        )
        user = User.objects.create_user(**self.user_data, username=generated_username)
        self.assertEqual(user.username, generated_username)
        self.assertTrue(user.check_password(self.user_data["password"]))
        self.assertEqual(user.first_name, self.user_data["first_name"])
        self.assertEqual(user.last_name, self.user_data["last_name"])
        self.assertEqual(user.phone, self.user_data["phone"])
        self.assertEqual(user.city, self.user_data["city"])
        self.assertEqual(user.zipcode, self.user_data["zipcode"])
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_username_uniqueness(self):
        generated_username1 = self.user_name_generator.generate_username(
            "uniqueuser1@example.com"
        )
        generated_username2 = self.user_name_generator.generate_username(
            "uniqueuser2@example.com"
        )
        User.objects.create_user(
            email="uniqueuser1@example.com",
            username=generated_username1,
            password="password123",
        )
        user2 = User.objects.create_user(
            email="uniqueuser2@example.com",
            username=generated_username2,
            password="password123",
        )
        self.assertNotEqual(generated_username1, generated_username2)
        self.assertNotEqual(
            User.objects.get(email="uniqueuser1@example.com").username, user2.username
        )

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
