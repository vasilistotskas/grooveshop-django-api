import datetime

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories import CartFactory
from cart.models import Cart
from user.factories.account import UserAccountFactory

User = get_user_model()


class CartViewSetTest(APITestCase):
    user: User = None
    cart: Cart = None
    detail_url: str = None
    update_data: dict = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.client.force_authenticate(user=self.user)
        self.cart = CartFactory(user=self.user, num_cart_items=0)
        self.detail_url = reverse("cart-detail")
        self.update_data = {
            "user": self.user.pk,
        }

    def _add_cart_headers(self, cart_id=None, session_key=None):
        headers = {}
        if cart_id:
            headers["HTTP_X_CART_ID"] = str(cart_id)
        if session_key:
            headers["HTTP_X_SESSION_KEY"] = session_key
        return headers

    def test_retrieve_cart(self):
        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.cart.pk)
        self.assertEqual(response.data["user"], self.user.pk)
        self.assertIn("last_activity", response.data)

    def test_update_cart(self):
        response = self.client.put(
            self.detail_url, data=self.update_data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.cart.refresh_from_db()
        self.assertTrue(
            self.cart.last_activity
            >= datetime.datetime(2023, 7, 26, 12, 0, tzinfo=datetime.UTC)
        )
        self.assertIn("last_activity", response.data)

    def test_partial_update_cart(self):
        partial_data = {"user": self.user.pk}
        response = self.client.patch(
            self.detail_url, data=partial_data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.cart.refresh_from_db()
        self.assertEqual(response.data["user"], self.user.pk)
        self.assertIn("last_activity", response.data)

    def test_delete_cart(self):
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())

    def test_retrieve_cart_as_anonymous_user_with_headers(self):
        self.client.force_authenticate(user=None)

        anonymous_cart = CartFactory(user=None, num_cart_items=0)

        headers = self._add_cart_headers(
            cart_id=anonymous_cart.id, session_key=anonymous_cart.session_key
        )

        response = self.client.get(self.detail_url, **headers)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], anonymous_cart.pk)
        self.assertIsNone(response.data["user"])
        self.assertIn("last_activity", response.data)

    def test_create_cart_for_anonymous_user_no_headers(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["id"])
        self.assertIsNone(response.data["user"])
        self.assertIsNotNone(response.data["session_key"])

    def test_update_cart_as_anonymous_user(self):
        self.client.force_authenticate(user=None)

        anonymous_cart = CartFactory(user=None, num_cart_items=0)

        headers = self._add_cart_headers(
            cart_id=anonymous_cart.id, session_key=anonymous_cart.session_key
        )

        update_data = {}
        response = self.client.patch(
            self.detail_url, data=update_data, format="json", **headers
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        anonymous_cart.refresh_from_db()
        self.assertIn("last_activity", response.data)

    def test_delete_cart_as_anonymous_user(self):
        self.client.force_authenticate(user=None)

        anonymous_cart = CartFactory(user=None, num_cart_items=0)

        headers = self._add_cart_headers(
            cart_id=anonymous_cart.id, session_key=anonymous_cart.session_key
        )

        response = self.client.delete(self.detail_url, **headers)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Cart.objects.filter(pk=anonymous_cart.pk).exists())

    def test_wrong_session_key_creates_new_cart(self):
        self.client.force_authenticate(user=None)

        anonymous_cart = CartFactory(user=None, num_cart_items=0)

        headers = self._add_cart_headers(
            cart_id=anonymous_cart.id, session_key="wrong-session-key"
        )

        response = self.client.get(self.detail_url, **headers)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(response.data["id"], anonymous_cart.pk)

    def test_authenticated_user_with_guest_cart_headers_merges(self):
        guest_cart = CartFactory(user=None, num_cart_items=0)

        headers = self._add_cart_headers(
            cart_id=guest_cart.id, session_key=guest_cart.session_key
        )

        response = self.client.get(self.detail_url, **headers)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"], self.user.pk)

        self.assertFalse(Cart.objects.filter(id=guest_cart.id).exists())

    def test_update_cart_with_invalid_data(self):
        invalid_update_data = {
            "user": 9999,
        }
        response = self.client.patch(
            self.detail_url, data=invalid_update_data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user", response.data)

    def test_partial_update_cart_with_extra_fields(self):
        extra_data = {
            "invalid_field": "should not be here",
        }
        response = self.client.patch(
            self.detail_url, data=extra_data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.cart.refresh_from_db()
        self.assertNotIn("invalid_field", response.data)
        self.assertIn("last_activity", response.data)
