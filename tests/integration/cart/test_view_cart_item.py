from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories import CartFactory, CartItemFactory
from cart.models import Cart, CartItem
from product.factories.product import ProductFactory
from product.models.product import Product
from user.factories.account import UserAccountFactory

User = get_user_model()


class CartItemViewSetTest(APITestCase):
    user: User = None
    cart: Cart = None
    product: Product = None
    cart_item: CartItem = None
    list_url: str = None
    detail_url: str = None
    create_data: dict = None
    update_data: dict = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.client.force_authenticate(user=self.user)

        self.cart = CartFactory(user=self.user, num_cart_items=0)
        self.cart.save()

        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.product.save()

        self.cart_item = CartItemFactory(
            cart=self.cart, product=self.product, quantity=2
        )
        self.cart_item.save()

        self.list_url = reverse("cart-item-list")
        self.detail_url = reverse(
            "cart-item-detail", kwargs={"pk": self.cart_item.pk}
        )

        self.create_data = {
            "cart": self.cart.pk,
            "product": self.product.pk,
            "quantity": 3,
        }
        self.update_data = {"quantity": 5}

        self.assertIsNotNone(self.cart, "Cart should not be None")
        self.assertEqual(
            self.cart.user,
            self.user,
            "Cart's user should match the authenticated user",
        )
        self.assertTrue(
            Cart.objects.filter(user=self.user).exists(),
            "Cart should exist for the user",
        )

    def test_list(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_create_cart_item(self):
        new_product = ProductFactory(num_images=0, num_reviews=0)
        new_product.save()

        create_data = {
            "cart": self.cart.pk,
            "product": new_product.pk,
            "quantity": 3,
        }

        response = self.client.post(
            self.list_url, data=create_data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CartItem.objects.count(), 2)

        cart_item = CartItem.objects.get(product=new_product)
        self.assertEqual(cart_item.quantity, 3)

    def test_retrieve_cart_item(self):
        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["quantity"], self.cart_item.quantity)

    def test_update_cart_item(self):
        response = self.client.patch(
            self.detail_url, data=self.update_data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.cart_item.refresh_from_db()
        self.assertEqual(self.cart_item.quantity, self.update_data["quantity"])

    def test_delete_cart_item(self):
        response = self.client.delete(self.detail_url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_create_cart_item_authenticated_user(self):
        self.client.force_authenticate(user=self.user)

        self.cart.items.all().delete()

        from product.factories.product import ProductFactory

        product = ProductFactory(num_images=0, num_reviews=0)

        data = {"product": product.pk, "quantity": 2}

        response = self.client.post(self.list_url, data=data, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["quantity"], 2)
        self.assertEqual(self.cart.items.count(), 1)

    def test_create_cart_item_anonymous_user(self):
        self.client.force_authenticate(user=None)

        anonymous_cart = CartFactory(user=None, num_cart_items=0)

        headers = {
            "HTTP_X_CART_ID": str(anonymous_cart.id),
            "HTTP_X_SESSION_KEY": anonymous_cart.session_key,
        }

        from product.factories.product import ProductFactory

        product = ProductFactory(num_images=0, num_reviews=0)

        data = {"product": product.pk, "quantity": 3}

        response = self.client.post(
            self.list_url, data=data, format="json", **headers
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["quantity"], 3)
        self.assertEqual(anonymous_cart.items.count(), 1)

    def test_update_cart_item_with_wrong_cart_forbidden(self):
        self.client.force_authenticate(user=self.user)

        other_user = UserAccountFactory(num_addresses=0)
        other_cart = CartFactory(user=other_user, num_cart_items=1)
        other_item = other_cart.items.first()

        update_url = reverse("cart-item-detail", kwargs={"pk": other_item.pk})

        response = self.client.patch(
            update_url, data={"quantity": 5}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_cart_items_only_shows_own_items(self):
        self.client.force_authenticate(user=self.user)

        self.cart.items.all().delete()

        from product.factories.product import ProductFactory

        product1 = ProductFactory(num_images=0, num_reviews=0)
        product2 = ProductFactory(num_images=0, num_reviews=0)

        from cart.models import CartItem

        CartItem.objects.create(cart=self.cart, product=product1, quantity=1)
        CartItem.objects.create(cart=self.cart, product=product2, quantity=2)

        CartFactory(user=None, num_cart_items=2)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        items = response.data["results"]

        self.assertEqual(len(items), 2)

        cart_ids = [item["cart"] for item in items]
        self.assertTrue(all(cart_id == self.cart.id for cart_id in cart_ids))
