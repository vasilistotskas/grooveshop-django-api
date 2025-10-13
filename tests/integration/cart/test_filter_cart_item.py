from django.urls import reverse
from rest_framework.test import APITestCase

from cart.factories import CartItemFactory
from cart.factories.cart import CartFactory


class CartItemFilterTest(APITestCase):
    def setUp(self):
        self.cart1 = CartFactory()
        self.cart_item1 = CartItemFactory(cart=self.cart1)
        self.cart_item2 = CartItemFactory()

    def test_activity_filters(self):
        url = reverse("cart-item-list")

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

    def test_camel_case_filters(self):
        url = reverse("cart-item-list")

        response = self.client.get(url, {"hasUser": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(
            url, {"createdAfter": "2024-01-01T00:00:00Z"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_cart_type_filters(self):
        url = reverse("cart-item-list")

        response = self.client.get(url, {"has_user": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"has_user": "false"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_complex_filter_combinations(self):
        url = reverse("cart-item-list")

        response = self.client.get(
            url, {"has_user": "true", "quantity__gte": 1}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(
            url, {"min_total_value": 1, "has_items": "true"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_filter_with_ordering(self):
        url = reverse("cart-item-list")

        response = self.client.get(url, {"ordering": "created_at"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"ordering": "-created_at"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_item_filters(self):
        url = reverse("cart-item-list")

        response = self.client.get(url, {"has_items": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"quantity__gte": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"min_items": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_session_filters(self):
        url = reverse("cart-item-list")

        response = self.client.get(url, {"cart__user__isnull": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"cart__user__isnull": "false"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_timestamp_filters(self):
        url = reverse("cart-item-list")

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

        response = self.client.get(
            url, {"updated_after": "2024-01-01T00:00:00Z"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_user_filters(self):
        url = reverse("cart-item-list")

        response = self.client.get(url, {"user": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"user__is_active": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_uuid_filter(self):
        url = reverse("cart-item-list")

        response = self.client.get(url, {"uuid": str(self.cart_item1.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_value_filters(self):
        url = reverse("cart-item-list")

        response = self.client.get(url, {"min_total_value": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"max_total_value": 1000})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(
            url, {"total_value__gte": 1, "total_value__lte": 1000}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)
