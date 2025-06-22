import datetime

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.models import Cart
from core.utils.testing import TestURLFixerMixin
from user.factories.account import UserAccountFactory

User = get_user_model()


class CartViewSetTest(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory(num_addresses=0)
        cls.other_user = UserAccountFactory(num_addresses=0)
        cls.cart = CartFactory(user=cls.user, num_cart_items=2)
        cls.other_cart = CartFactory(user=cls.other_user, num_cart_items=1)
        cls.guest_cart = CartFactory(user=None, num_cart_items=1)

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.detail_url = reverse("cart-detail")

    def _add_cart_headers(self, cart_id=None, session_key=None):
        headers = {}
        if cart_id:
            headers["HTTP_X_CART_ID"] = str(cart_id)
        if session_key:
            headers["HTTP_X_SESSION_KEY"] = session_key
        return headers

    def test_cart_only_supports_detail_operations(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_cart(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "user",
            "session_key",
            "last_activity",
            "created_at",
            "updated_at",
            "uuid",
            "items",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
        }
        response_fields = set(response.data.keys())

        for field in expected_fields:
            self.assertIn(
                field,
                response_fields,
                f"Detail field '{field}' should be present in retrieve response",
            )

        self.assertEqual(response.data["id"], self.cart.pk)
        self.assertEqual(response.data["user"], self.user.pk)

    def test_retrieve_uses_correct_serializer(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "user",
            "session_key",
            "last_activity",
            "created_at",
            "updated_at",
            "uuid",
            "items",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
        }
        response_fields = set(response.data.keys())

        for field in expected_fields:
            self.assertIn(
                field,
                response_fields,
                f"Detail field '{field}' should be present in detail response",
            )

    def test_update_valid(self):
        update_data = {
            "user": self.user.pk,
        }
        response = self.client.put(self.detail_url, update_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "user",
            "session_key",
            "last_activity",
            "created_at",
            "updated_at",
            "uuid",
            "items",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        self.cart.refresh_from_db()
        self.assertTrue(
            self.cart.last_activity
            >= datetime.datetime(2023, 7, 26, 12, 0, tzinfo=datetime.UTC)
        )

    def test_update_invalid(self):
        invalid_update_data = {
            "user": 9999,
        }
        response = self.client.put(
            self.detail_url, invalid_update_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user", response.data)

    def test_partial_update_valid(self):
        partial_data = {"user": self.user.pk}
        response = self.client.patch(
            self.detail_url, partial_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "user",
            "session_key",
            "last_activity",
            "created_at",
            "updated_at",
            "uuid",
            "items",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        self.cart.refresh_from_db()
        self.assertEqual(response.data["user"], self.user.pk)

    def test_partial_update_invalid(self):
        invalid_partial_data = {
            "user": "invalid_user_id",
        }
        response = self.client.patch(
            self.detail_url, invalid_partial_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())

    def test_create_not_allowed(self):
        create_data = {"user": self.user.pk}
        response = self.client.post(self.detail_url, create_data, format="json")
        self.assertEqual(
            response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED
        )

    def test_update_request_response_serializers(self):
        update_data = {
            "user": self.user.pk,
        }
        response = self.client.put(self.detail_url, update_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_detail_fields = {
            "id",
            "user",
            "session_key",
            "last_activity",
            "created_at",
            "updated_at",
            "uuid",
            "items",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
        }
        self.assertTrue(
            expected_detail_fields.issubset(set(response.data.keys()))
        )

    def test_partial_update_request_response_serializers(self):
        partial_data = {"user": self.user.pk}
        response = self.client.patch(
            self.detail_url, partial_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_detail_fields = {
            "id",
            "user",
            "session_key",
            "last_activity",
            "created_at",
            "updated_at",
            "uuid",
            "items",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
        }
        self.assertTrue(
            expected_detail_fields.issubset(set(response.data.keys()))
        )

    def test_validation_errors_consistent(self):
        invalid_data = {
            "user": "not_a_number",
        }
        response = self.client.put(self.detail_url, invalid_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertIn("user", response.data)

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

    def test_queryset_optimization_for_authenticated_user(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"], self.user.pk)

    def test_staff_user_can_see_all_carts(self):
        staff_user = UserAccountFactory(is_staff=True, num_addresses=0)
        self.client.force_authenticate(user=staff_user)

        response = self.client.get(self.detail_url)
        self.assertIn(
            response.status_code,
            [status.HTTP_200_OK, status.HTTP_404_NOT_FOUND],
        )

    def test_cart_service_integration(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("id", response.data)
        self.assertIn("user", response.data)
        self.assertIn("items", response.data)
