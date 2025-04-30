import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.handlers.wsgi import WSGIRequest
from django.test import RequestFactory, TestCase

from cart.factories import CartFactory, CartItemFactory
from cart.models import Cart, CartItem
from cart.services import (
    CartService,
    InvalidProcessCartOptionException,
    ProcessCartOption,
)
from core.caches import cache_instance
from product.factories.product import ProductFactory
from product.models.product import Product
from user.factories.account import UserAccountFactory

User = get_user_model()


class CartServiceTest(TestCase):
    user: User = None
    factory: RequestFactory = None
    request: WSGIRequest = None
    cart: Cart = None
    product: Product = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = self.user

        self.cart = CartFactory(user=self.user, num_cart_items=0)
        self.product = ProductFactory(num_images=0, num_reviews=0)

        CartItem.objects.all().delete()

        self.request.session = {"cart_id": self.cart.pk}

    def test_get_cart_item(self):
        cart_service = CartService(request=self.request)
        cart_service.create_cart_item(self.product, 2)
        cart_item = cart_service.get_cart_item(self.product.pk)
        self.assertEqual(cart_item.product, self.product)

    def test_create_cart_item(self):
        CartItem.objects.all().delete()
        cart_service = CartService(request=self.request)
        cart_item = cart_service.create_cart_item(self.product, 3)
        self.assertEqual(cart_item.quantity, 3)
        self.assertEqual(CartItem.objects.count(), 1)

    def test_update_cart_item(self):
        cart_service = CartService(request=self.request)
        cart_service.create_cart_item(self.product, 2)
        cart_item = cart_service.update_cart_item(self.product.pk, 5)
        self.assertEqual(cart_item.quantity, 5)

    def test_delete_cart_item(self):
        cart_service = CartService(request=self.request)
        cart_service.create_cart_item(self.product, 2)
        cart_service.delete_cart_item(self.product.pk)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_process_cart_merge(self):
        cart_service = CartService(request=self.request)
        pre_login_cart = CartFactory(user=None, num_cart_items=0)
        cart_service.create_cart_item(self.product, 2)
        cache_instance.set(str(self.user.id), pre_login_cart.id, 3600)

        cart_service.process_cart(option=ProcessCartOption.MERGE)

        self.assertEqual(self.cart.items.count(), 1)
        self.assertEqual(self.cart.items.first().quantity, 2)

    def test_process_cart_merge_with_pre_login_cart_in_session(self):
        cart_service = CartService(request=self.request)
        pre_login_cart = CartFactory(user=None, num_cart_items=0)
        cart_service.create_cart_item(self.product, 2)
        self.request.session = {"pre_log_in_cart_id": pre_login_cart.id}

        cart_service.process_cart(option=ProcessCartOption.MERGE)

        self.assertEqual(self.cart.items.count(), 1)
        self.assertEqual(self.cart.items.first().quantity, 2)

    def test_process_cart_keep(self):
        cart_service = CartService(request=self.request)
        pre_login_cart = CartFactory(user=None, num_cart_items=0)
        cart_service.create_cart_item(self.product, 3)
        cache_instance.set(str(self.user.id), pre_login_cart.id, 3600)

        cart_service.process_cart(option=ProcessCartOption.KEEP)

        self.assertEqual(self.cart.items.count(), 1)
        self.assertEqual(self.cart.items.first().quantity, 3)

    def test_process_cart_clean(self):
        cart_service = CartService(request=self.request)
        cart_service.create_cart_item(self.product, 2)

        cart_service.process_cart(option=ProcessCartOption.CLEAN)

        self.assertEqual(self.cart.items.count(), 0)

    def test_process_cart_invalid_option(self):
        cart_service = CartService(request=self.request)
        with self.assertRaises(InvalidProcessCartOptionException):
            cart_service.process_cart(option="invalid_option")

    def test_get_cart_by_id_valid(self):
        cart_service = CartService(request=self.request)
        cart_id = self.cart.id

        cart = cart_service.get_cart_by_id(cart_id)

        self.assertEqual(cart, self.cart)

    def test_get_cart_by_id_invalid(self):
        cart_service = CartService(request=self.request)
        cart_id = self.cart.id + 1

        cart = cart_service.get_cart_by_id(cart_id)

        self.assertIsNone(cart)

    def test_cart_service_string_representation(self):
        cart_service = CartService(request=self.request)
        self.assertEqual(str(cart_service), f"Cart {self.user}")

    def test_cart_service_length(self):
        cart_service = CartService(request=self.request)
        cart_service.create_cart_item(self.product, 3)

        self.assertEqual(len(cart_service), 3)

    def test_cart_service_iteration(self):
        cart_service = CartService(request=self.request)
        cart_service.create_cart_item(self.product, 2)

        cart_items = list(cart_service)

        self.assertEqual(len(cart_items), 1)
        self.assertEqual(cart_items[0].product, self.product)

    def test_cart_service_add_more_items(self):
        cart_service = CartService(request=self.request)

        product = ProductFactory(num_images=0, num_reviews=0)

        cart_service.create_cart_item(self.product, 3)
        cart_service.create_cart_item(product, 4)

        self.assertEqual(len(cart_service), 7)
        self.assertEqual(self.cart.items.count(), 2)

    def test_cart_service_remove_item(self):
        cart_service = CartService(request=self.request)
        cart_service.create_cart_item(self.product, 2)

        cart_service.delete_cart_item(self.product.pk)

        self.assertEqual(len(cart_service), 0)
        self.assertEqual(self.cart.items.count(), 0)

    def test_merge_carts_with_items_in_both_carts(self):
        cart_service = CartService(request=self.request)

        pre_login_cart = CartFactory(user=None, num_cart_items=0)
        pre_login_product = ProductFactory(num_images=0, num_reviews=0)
        CartItemFactory(
            cart=pre_login_cart, product=pre_login_product, quantity=1
        )
        cart_service.create_cart_item(self.product, 2)

        self.request.session = {"pre_log_in_cart_id": pre_login_cart.id}

        cart_service.process_cart(option=ProcessCartOption.MERGE)

        self.assertEqual(self.cart.items.count(), 2)
        self.assertCountEqual(
            [item.product for item in self.cart.items.all()],
            [self.product, pre_login_product],
        )


class GuestCartServiceTest(TestCase):
    factory: RequestFactory = None
    request: WSGIRequest = None
    session_key: str = None
    product: Product = None

    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = AnonymousUser()
        self.session_key = str(uuid.uuid4())

        self.request.session = {
            "cart_id": None,
            "pre_log_in_cart_id": None,
        }

        self.product = ProductFactory(num_images=0, num_reviews=0)

    def test_get_or_create_cart_for_guest(self):
        """Test that get_or_create_cart creates a cart for guest users."""
        guest_cart = CartFactory(user=None, session_key=self.session_key)

        self.request.session["cart_id"] = guest_cart.id

        cart_service = CartService(request=self.request)
        cart = cart_service.get_or_create_cart()

        self.assertIsNotNone(cart)
        self.assertIsNone(cart.user)
        self.assertEqual(cart.id, guest_cart.id)
        self.assertEqual(self.request.session.get("cart_id"), cart.id)

    def test_create_cart_item_for_guest(self):
        """Test that a guest user can add items to their cart."""
        guest_cart = CartFactory(user=None, session_key=self.session_key)
        self.request.session["cart_id"] = guest_cart.id

        cart_service = CartService(request=self.request)
        cart_item = cart_service.create_cart_item(self.product, 3)

        self.assertEqual(cart_item.quantity, 3)
        self.assertEqual(cart_item.product, self.product)
        self.assertIsNone(cart_item.cart.user)
        self.assertEqual(cart_item.cart.id, guest_cart.id)

    def test_merge_guest_cart_to_user_cart(self):
        """Test merging a guest cart into a user cart when logging in."""
        guest_cart = CartFactory(user=None, session_key=self.session_key)

        cart_item = CartItemFactory(
            cart=guest_cart, product=self.product, quantity=2
        )

        self.assertEqual(cart_item.quantity, 2)
        self.assertEqual(guest_cart.items.count(), 1)

        user = UserAccountFactory(num_addresses=0)
        self.request.user = user

        user_cart = CartFactory(user=user)

        self.request.session = {
            "cart_id": user_cart.id,
            "pre_log_in_cart_id": guest_cart.id,
        }

        cart_item.cart = user_cart
        cart_item.save()

        guest_cart.delete()

        user_cart.refresh_from_db()
        self.assertEqual(user_cart.user, user)
        self.assertEqual(user_cart.items.count(), 1)
        self.assertEqual(user_cart.items.first().product, self.product)
        self.assertEqual(user_cart.items.first().quantity, 2)

        self.assertFalse(Cart.objects.filter(id=guest_cart.id).exists())

    def test_guest_cart_string_representation(self):
        """Test the string representation of a guest cart service."""
        guest_cart = CartFactory(user=None, session_key=self.session_key)
        self.request.session["cart_id"] = guest_cart.id

        cart_service = CartService(request=self.request)

        self.assertTrue(str(cart_service).startswith("Cart Anonymous"))

    def test_guest_cart_with_existing_session_key(self):
        """Test that a guest cart is retrieved if it exists for the session key."""
        existing_cart = CartFactory(user=None, session_key=self.session_key)

        self.request.session["cart_id"] = existing_cart.id

        cart_service = CartService(request=self.request)

        self.assertEqual(cart_service.cart.id, existing_cart.id)
