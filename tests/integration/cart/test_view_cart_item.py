from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.models import Cart
from cart.models import CartItem
from product.models.product import Product

User = get_user_model()


class CartItemViewSetTest(APITestCase):
    user: User = None
    cart: Cart = None
    product: Product = None
    cart_item: CartItem = None
    detail_url: str = None
    update_data: dict = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        self.client.login(email="testuser@example.com", password="testpassword")
        self.cart = Cart.objects.create(user=self.user)
        self.product = Product.objects.create(
            name="Test Product",
            slug="test-product",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=5.00,
            hits=0,
            weight=0.00,
        )
        self.cart_item = CartItem.objects.create(
            cart=self.cart, product=self.product, quantity=2
        )
        self.list_url = reverse("cart-item-list")
        self.detail_url = reverse("cart-item-detail", kwargs={"pk": self.cart_item.pk})
        self.create_data = {"product": self.product.pk, "quantity": 3}
        self.update_data = {"quantity": 5}

    def test_list(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_create_cart_item(self):
        response = self.client.post(self.list_url, data=self.create_data, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CartItem.objects.count(), 1)

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

    def tearDown(self) -> None:
        super().tearDown()
        self.cart_item.delete()
        self.cart.delete()
        self.user.delete()
        self.product.delete()
