from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.test import TestCase

from cart.models import Cart
from cart.models import CartItem
from cart.service import CartService
from core import caches
from product.models.product import Product

User = get_user_model()


class CartServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = self.user

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

        self.request.session = {"cart_id": self.cart.pk}

    def test_get_cart_item(self):
        cart_service = CartService(self.request)
        cart_item = cart_service.get_cart_item(self.product.pk)
        self.assertIsNone(cart_item)

        cart_service.create_cart_item(self.product, 2)
        cart_item = cart_service.get_cart_item(self.product.pk)
        self.assertEqual(cart_item.quantity, 2)

    def test_create_cart_item(self):
        cart_service = CartService(self.request)
        cart_item = cart_service.create_cart_item(self.product, 3)
        self.assertEqual(cart_item.quantity, 3)
        self.assertEqual(CartItem.objects.count(), 1)

    def test_update_cart_item(self):
        cart_service = CartService(self.request)
        cart_service.create_cart_item(self.product, 2)
        cart_item = cart_service.update_cart_item(self.product.pk, 5)
        self.assertEqual(cart_item.quantity, 5)

    def test_delete_cart_item(self):
        cart_service = CartService(self.request)
        cart_service.create_cart_item(self.product, 2)
        cart_service.delete_cart_item(self.product.pk)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_process_user_cart_merge(self):
        cart_service = CartService(self.request)
        # Create a cart for the pre-login user and add a cart item
        pre_login_cart = Cart.objects.create()
        cart_service.create_cart_item(self.product, 2)
        caches.set(str(self.user.id), pre_login_cart.id, 3600)

        # Merge carts with "merge" option
        cart_service.process_user_cart(self.request, option="merge")

        # Check that the cart items are now associated with the user's cart
        self.assertEqual(self.cart.cart_item_cart.count(), 1)
        self.assertEqual(self.cart.cart_item_cart.first().quantity, 2)

    def test_process_user_cart_clean(self):
        cart_service = CartService(self.request)
        # Create a cart item for the user's cart
        cart_service.create_cart_item(self.product, 2)

        # Clean user's cart with "clean" option
        cart_service.process_user_cart(self.request, option="clean")

        # Check that the cart items in the user's cart are deleted
        self.assertEqual(self.cart.cart_item_cart.count(), 0)

    def test_process_user_cart_invalid_option(self):
        cart_service = CartService(self.request)
        # Test with an invalid option
        with self.assertRaises(ValueError):
            cart_service.process_user_cart(self.request, option="invalid_option")

    def test_get_cart_by_id_valid(self):
        cart_service = CartService(self.request)
        cart_id = self.cart.id

        cart = cart_service.get_cart_by_id(cart_id)

        self.assertEqual(cart, self.cart)

    def test_get_cart_by_id_invalid(self):
        cart_service = CartService(self.request)
        cart_id = self.cart.id + 1

        cart = cart_service.get_cart_by_id(cart_id)

        self.assertIsNone(cart)

    def test_cart_service_string_representation(self):
        cart_service = CartService(self.request)
        self.assertEqual(str(cart_service), f"Cart {self.user}")

    def test_cart_service_length(self):
        cart_service = CartService(self.request)
        cart_service.create_cart_item(self.product, 3)

        self.assertEqual(len(cart_service), 3)

    def test_cart_service_iteration(self):
        cart_service = CartService(self.request)
        cart_service.create_cart_item(self.product, 2)

        cart_items = list(cart_service)

        self.assertEqual(len(cart_items), 1)
        self.assertEqual(cart_items[0].product, self.product)

    def test_cart_service_add_more_items(self):
        cart_service = CartService(self.request)

        product = Product.objects.create(
            name="New Product",
            slug="new-product",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=5.00,
            hits=0,
            weight=0.00,
        )

        # Add more items to the cart
        cart_service.create_cart_item(self.product, 3)
        cart_service.create_cart_item(product, 4)

        self.assertEqual(len(cart_service), 7)
        self.assertEqual(self.cart.cart_item_cart.count(), 2)

    def test_cart_service_remove_item(self):
        cart_service = CartService(self.request)
        cart_service.create_cart_item(self.product, 2)

        # Remove the cart item
        cart_service.delete_cart_item(self.product.pk)

        self.assertEqual(len(cart_service), 0)
        self.assertEqual(self.cart.cart_item_cart.count(), 0)
