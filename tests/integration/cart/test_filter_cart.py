from decimal import Decimal

from django.urls import reverse
from djmoney.money import Money
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from product.factories.product import ProductFactory
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


class CartHasDiscountsFilterTest(APITestCase):
    """`?hasDiscounts=` used to 500 — and nothing tested it.

    `CartItem.discount_value` is a PROPERTY, so the lookup it was built
    on could never resolve. The whole filter was unreferenced outside its
    own definition, which is why a `FieldError` sat there unnoticed.
    """

    def setUp(self):
        self.user = UserAccountFactory(is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=self.user)

        # CartFactory has `django_get_or_create = ("user",)` and defaults
        # the user to a shared get-or-create helper, so two bare
        # CartFactory() calls collapse onto ONE row. Distinct users keep
        # them distinct.
        self.discounted_cart = CartFactory(user=UserAccountFactory())
        CartItemFactory(
            cart=self.discounted_cart,
            product=ProductFactory(
                price=Money("20.00", "EUR"), discount_percent=Decimal("15.00")
            ),
            quantity=1,
        )
        self.plain_cart = CartFactory(user=UserAccountFactory())
        CartItemFactory(
            cart=self.plain_cart,
            product=ProductFactory(
                price=Money("20.00", "EUR"), discount_percent=Decimal("0.00")
            ),
            quantity=1,
        )

    def _ids(self, value):
        response = self.client.get(
            reverse("cart-list"), {"has_discounts": value}
        )
        self.assertEqual(response.status_code, 200)
        return {row["id"] for row in response.data["results"]}

    def test_true_returns_only_carts_holding_a_discounted_item(self):
        ids = self._ids("true")
        self.assertIn(self.discounted_cart.id, ids)
        self.assertNotIn(self.plain_cart.id, ids)

    def test_false_returns_only_carts_without_one(self):
        ids = self._ids("false")
        self.assertIn(self.plain_cart.id, ids)
        self.assertNotIn(self.discounted_cart.id, ids)
