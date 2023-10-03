# cart/tests/test_view_cart.py
import datetime

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.models import Cart

User = get_user_model()


class CartViewSetTest(APITestCase):
    user: User = None
    cart: Cart = None
    detail_url: str = None
    update_data: dict = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        self.client.login(email="testuser@example.com", password="testpassword")
        self.cart = Cart.objects.create(user=self.user)
        self.detail_url = reverse("cart-detail")
        self.update_data = {
            "user": self.user.pk,
            "last_activity": "2023-07-26T12:00:00.000000Z",
        }

        # Set the last_activity of the cart to match the update_data timestamp
        self.cart.last_activity = datetime.datetime(
            2023, 7, 26, 12, 0, tzinfo=datetime.timezone.utc
        )
        self.cart.save()

    def test_retrieve_cart(self):
        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.cart.pk)

    def test_update_cart(self):
        response = self.client.put(
            self.detail_url, data=self.update_data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_cart(self):
        response = self.client.patch(
            self.detail_url, data=self.update_data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_delete_cart(self):
        response = self.client.delete(self.detail_url)
        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def tearDown(self) -> None:
        super().tearDown()
        self.cart.delete()
        self.user.delete()
