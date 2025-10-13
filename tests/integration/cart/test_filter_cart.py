from django.urls import reverse
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from user.factories.account import UserAccountFactory


class CartFilterTest(APITestCase):
    def setUp(self):
        self.user = UserAccountFactory(is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=self.user)

        self.cart1 = CartFactory()
        self.cart2 = CartFactory()

    def test_activity_filters(self):
        url = reverse("cart-list")

        response = self.client.get(
            url, {"last_activity_after": "2024-01-01T00:00:00Z"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(
            url, {"last_activity_before": "2025-12-31T23:59:59Z"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_user_filters(self):
        url = reverse("cart-list")

        response = self.client.get(url, {"user": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"has_user": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_session_filters(self):
        url = reverse("cart-list")

        response = self.client.get(url, {"user__isnull": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"user__isnull": "false"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_cart_type_filters(self):
        url = reverse("cart-list")

        response = self.client.get(url, {"cart_type": "user"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"cart_type": "guest"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_item_filters(self):
        url = reverse("cart-list")

        response = self.client.get(url, {"has_items": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"min_items": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"max_items": 10})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_value_filters(self):
        url = reverse("cart-list")

        response = self.client.get(url, {"min_total_price": "10.00"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"max_total_price": "1000.00"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_timestamp_filters(self):
        url = reverse("cart-list")

        response = self.client.get(
            url, {"created_after": "2024-01-01T00:00:00Z"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(
            url, {"created_before": "2025-12-31T23:59:59Z"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_uuid_filter(self):
        url = reverse("cart-list")

        response = self.client.get(url, {"uuid": str(self.cart1.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_camel_case_filters(self):
        url = reverse("cart-list")

        response = self.client.get(
            url, {"hasUser": "true", "hasItems": "true", "cartType": "user"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_complex_filter_combinations(self):
        url = reverse("cart-list")

        response = self.client.get(
            url,
            {
                "has_user": "true",
                "has_items": "true",
                "min_total_price": "10.00",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_filter_with_ordering(self):
        url = reverse("cart-list")

        response = self.client.get(
            url, {"has_items": "true", "ordering": "-created_at"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)
