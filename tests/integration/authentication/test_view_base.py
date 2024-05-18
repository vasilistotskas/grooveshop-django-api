from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

User = get_user_model()


class IsUserRegisteredTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )

    def test_is_user_registered(self):
        response = self.client.post(
            reverse("is_user_registered"), data={"email": "testuser@example.com"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["registered"], True)

    def test_is_user_not_registered(self):
        response = self.client.post(
            reverse("is_user_registered"), data={"email": "notregistered@example.com"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["registered"], False)
