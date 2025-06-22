from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from cart.models import CartItem
from core.utils.testing import TestURLFixerMixin
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

User = get_user_model()


class CartItemViewSetTest(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory(num_addresses=0)
        cls.other_user = UserAccountFactory(num_addresses=0)

        cls.cart = CartFactory(user=cls.user, num_cart_items=0)
        cls.other_cart = CartFactory(user=cls.other_user, num_cart_items=0)

        cls.product1 = ProductFactory(
            active=True, num_images=0, num_reviews=0, stock=10
        )
        cls.product2 = ProductFactory(
            active=True, num_images=0, num_reviews=0, stock=5
        )

        cls.cart_item = CartItemFactory(
            cart=cls.cart, product=cls.product1, quantity=2
        )
        cls.other_cart_item = CartItemFactory(
            cart=cls.other_cart, product=cls.product2, quantity=3
        )

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.list_url = reverse("cart-item-list")
        self.detail_url = reverse(
            "cart-item-detail", kwargs={"pk": self.cart_item.pk}
        )

    def test_list_uses_correct_serializer(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertEqual(len(response.data["results"]), 1)

        item_data = response.data["results"][0]

        expected_fields = {
            "id",
            "uuid",
            "cart_id",
            "product",
            "quantity",
            "price",
            "final_price",
            "discount_value",
            "total_price",
            "total_discount_value",
            "vat_value",
            "vat_percent",
            "discount_percent",
            "price_save_percent",
            "created_at",
            "updated_at",
        }
        self.assertTrue(expected_fields.issubset(set(item_data.keys())))

    def test_retrieve_uses_correct_serializer(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "uuid",
            "cart_id",
            "product",
            "quantity",
            "price",
            "final_price",
            "discount_value",
            "total_price",
            "total_discount_value",
            "vat_value",
            "vat_percent",
            "discount_percent",
            "price_save_percent",
            "created_at",
            "updated_at",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_uses_correct_serializer(self):
        new_product = ProductFactory(active=True, num_images=0, num_reviews=0)
        create_data = {
            "product": new_product.pk,
            "quantity": 3,
        }

        response = self.client.post(self.list_url, create_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_fields = {
            "id",
            "uuid",
            "cart_id",
            "product",
            "quantity",
            "price",
            "final_price",
            "discount_value",
            "total_price",
            "total_discount_value",
            "vat_value",
            "vat_percent",
            "discount_percent",
            "price_save_percent",
            "created_at",
            "updated_at",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_update_uses_correct_serializer(self):
        update_data = {"quantity": 5}

        response = self.client.patch(
            self.detail_url, update_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "uuid",
            "cart_id",
            "product",
            "quantity",
            "price",
            "final_price",
            "discount_value",
            "total_price",
            "total_discount_value",
            "vat_value",
            "vat_percent",
            "discount_percent",
            "price_save_percent",
            "created_at",
            "updated_at",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_list_cart_items(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], self.cart_item.id)

    def test_retrieve_cart_item(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.cart_item.id)
        self.assertEqual(response.data["quantity"], self.cart_item.quantity)

    def test_create_cart_item(self):
        new_product = ProductFactory(active=True, num_images=0, num_reviews=0)
        create_data = {
            "product": new_product.pk,
            "quantity": 3,
        }

        response = self.client.post(self.list_url, create_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertTrue(
            CartItem.objects.filter(
                cart=self.cart, product=new_product, quantity=3
            ).exists()
        )

    def test_update_cart_item(self):
        update_data = {"quantity": 5}

        response = self.client.patch(
            self.detail_url, update_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.cart_item.refresh_from_db()
        self.assertEqual(self.cart_item.quantity, 5)

    def test_delete_cart_item(self):
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertFalse(CartItem.objects.filter(id=self.cart_item.id).exists())

    def test_cannot_access_other_user_cart_items(self):
        other_item_url = reverse(
            "cart-item-detail", kwargs={"pk": self.other_cart_item.pk}
        )

        response = self.client.get(other_item_url)
        self.assertIn(
            response.status_code,
            [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND],
        )

    def test_cannot_update_other_user_cart_items(self):
        other_item_url = reverse(
            "cart-item-detail", kwargs={"pk": self.other_cart_item.pk}
        )

        response = self.client.patch(
            other_item_url, {"quantity": 10}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_delete_other_user_cart_items(self):
        other_item_url = reverse(
            "cart-item-detail", kwargs={"pk": self.other_cart_item.pk}
        )

        response = self.client.delete(other_item_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_cart_item_guest_user(self):
        self.client.force_authenticate(user=None)

        guest_cart = CartFactory(user=None, num_cart_items=0)
        product = ProductFactory(active=True, num_images=0, num_reviews=0)

        headers = {
            "HTTP_X_CART_ID": str(guest_cart.id),
            "HTTP_X_SESSION_KEY": guest_cart.session_key,
        }

        create_data = {
            "product": product.pk,
            "quantity": 2,
        }

        response = self.client.post(
            self.list_url, create_data, format="json", **headers
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertTrue(
            CartItem.objects.filter(
                cart=guest_cart, product=product, quantity=2
            ).exists()
        )

    def test_list_guest_cart_items(self):
        self.client.force_authenticate(user=None)

        guest_cart = CartFactory(user=None, num_cart_items=1)

        headers = {
            "HTTP_X_CART_ID": str(guest_cart.id),
            "HTTP_X_SESSION_KEY": guest_cart.session_key,
        }

        response = self.client.get(self.list_url, **headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_filter_by_product(self):
        response = self.client.get(self.list_url, {"product": self.product1.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["product"]["id"], self.product1.id
        )

    def test_filter_by_quantity_range(self):
        CartItemFactory(cart=self.cart, quantity=1)
        CartItemFactory(cart=self.cart, quantity=5)

        response = self.client.get(self.list_url, {"min_quantity": 2})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        quantities = [item["quantity"] for item in response.data["results"]]
        self.assertTrue(all(q >= 2 for q in quantities))

        response = self.client.get(self.list_url, {"max_quantity": 3})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        quantities = [item["quantity"] for item in response.data["results"]]
        self.assertTrue(all(q <= 3 for q in quantities))

    def test_filter_by_product_name(self):
        self.product1.set_current_language("en")
        self.product1.name = "Test Product Name"
        self.product1.save()

        response = self.client.get(
            self.list_url, {"product_name": "Test Product"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_ordering_by_quantity(self):
        CartItemFactory(cart=self.cart, quantity=1)
        CartItemFactory(cart=self.cart, quantity=5)

        response = self.client.get(self.list_url, {"ordering": "quantity"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        quantities = [item["quantity"] for item in response.data["results"]]
        self.assertEqual(quantities, sorted(quantities))

    def test_ordering_by_created_at_desc(self):
        response = self.client.get(self.list_url, {"ordering": "-created_at"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreater(len(response.data["results"]), 0)

    def test_search_by_product_name(self):
        self.product1.set_current_language("en")
        self.product1.name = "Searchable Product"
        self.product1.save()

        response = self.client.get(self.list_url, {"search": "Searchable"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_create_with_invalid_quantity(self):
        create_data = {
            "product": self.product1.pk,
            "quantity": 0,
        }

        response = self.client.post(self.list_url, create_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_with_nonexistent_product(self):
        create_data = {
            "product": 99999,
            "quantity": 1,
        }

        response = self.client.post(self.list_url, create_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_with_invalid_quantity(self):
        update_data = {"quantity": -1}

        response = self.client.patch(
            self.detail_url, update_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_nonexistent_cart_item(self):
        nonexistent_url = reverse("cart-item-detail", kwargs={"pk": 99999})

        response = self.client.get(nonexistent_url)
        self.assertIn(
            response.status_code,
            [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND],
        )

    def test_unauthenticated_access_without_guest_headers(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 0)

    def test_cart_item_price_calculations(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        item_data = response.data
        self.assertIn("price", item_data)
        self.assertIn("final_price", item_data)
        self.assertIn("total_price", item_data)
        self.assertIn("discount_value", item_data)
        self.assertIn("vat_value", item_data)

    def test_duplicate_cart_item_handling(self):
        create_data = {
            "product": self.product1.pk,
            "quantity": 1,
        }

        response = self.client.post(self.list_url, create_data, format="json")
        self.assertIn(
            response.status_code,
            [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_201_CREATED,
            ],
        )
