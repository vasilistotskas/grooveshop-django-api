import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from cart.models import Cart, CartItem
from cart.services import (
    CartService,
)
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

User = get_user_model()


class CartServiceTest(TestCase):
    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.factory = RequestFactory()
        self.product = ProductFactory(num_images=0, num_reviews=0)

        self.cart = CartFactory(user=self.user, num_cart_items=0)
        CartItem.objects.all().delete()

        self.request = self._create_request_with_headers(
            user=self.user,
            cart_id=self.cart.id,
            session_key=None,
        )

    def _create_request_with_headers(
        self, user=None, cart_id=None, session_key=None
    ):
        request = self.factory.get("/")
        request.user = user or AnonymousUser()

        if cart_id:
            request.META["HTTP_X_CART_ID"] = str(cart_id)
        if session_key:
            request.META["HTTP_X_SESSION_KEY"] = session_key

        request.session = {}

        return request

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

    def test_process_cart_merge_with_guest_cart_headers(self):
        guest_cart = CartFactory(user=None, num_cart_items=0)
        guest_product = ProductFactory(num_images=0, num_reviews=0)
        CartItemFactory(cart=guest_cart, product=guest_product, quantity=2)

        request = self._create_request_with_headers(
            user=self.user,
            cart_id=guest_cart.id,
            session_key=guest_cart.session_key,
        )

        CartService(request=request)

        self.assertEqual(self.cart.items.count(), 1)
        self.assertEqual(self.cart.items.first().quantity, 2)
        self.assertEqual(self.cart.items.first().product, guest_product)

        self.assertFalse(Cart.objects.filter(id=guest_cart.id).exists())

    def test_get_cart_by_id_valid(self):
        cart_service = CartService(request=self.request)
        cart_id = self.cart.id

        cart = cart_service.get_cart_by_id(cart_id)

        self.assertEqual(cart, self.cart)

    def test_get_cart_by_id_invalid(self):
        cart_service = CartService(request=self.request)
        cart_id = self.cart.id + 1000

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
        cart_service.create_cart_item(self.product, 2)

        guest_cart = CartFactory(user=None, num_cart_items=0)
        guest_product = ProductFactory(num_images=0, num_reviews=0)
        CartItemFactory(cart=guest_cart, product=guest_product, quantity=1)

        cart_service.merge_carts(guest_cart)

        self.assertEqual(self.cart.items.count(), 2)
        self.assertCountEqual(
            [item.product for item in self.cart.items.all()],
            [self.product, guest_product],
        )


class GuestCartServiceTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.session_key = str(uuid.uuid4())
        self.product = ProductFactory(num_images=0, num_reviews=0)

    def _create_guest_request(self, cart_id=None, session_key=None):
        request = self.factory.get("/")
        request.user = AnonymousUser()

        if cart_id:
            request.META["HTTP_X_CART_ID"] = str(cart_id)
        if session_key:
            request.META["HTTP_X_SESSION_KEY"] = session_key

        request.session = {}
        return request

    def test_get_or_create_cart_for_new_guest(self):
        request = self._create_guest_request(session_key=self.session_key)

        cart_service = CartService(request=request)
        cart = cart_service.get_or_create_cart()

        self.assertIsNotNone(cart)
        self.assertIsNone(cart.user)
        self.assertEqual(cart.session_key, self.session_key)

    def test_get_or_create_cart_for_guest_with_existing_cart(self):
        guest_cart = CartFactory(user=None, session_key=self.session_key)

        request = self._create_guest_request(
            cart_id=guest_cart.id, session_key=self.session_key
        )

        cart_service = CartService(request=request)
        cart = cart_service.get_or_create_cart()

        self.assertIsNotNone(cart)
        self.assertEqual(cart.id, guest_cart.id)
        self.assertEqual(cart.session_key, self.session_key)

    def test_create_cart_item_for_guest(self):
        guest_cart = CartFactory(user=None, session_key=self.session_key)
        request = self._create_guest_request(
            cart_id=guest_cart.id, session_key=self.session_key
        )

        cart_service = CartService(request=request)
        cart_item = cart_service.create_cart_item(self.product, 3)

        self.assertEqual(cart_item.quantity, 3)
        self.assertEqual(cart_item.product, self.product)
        self.assertIsNone(cart_item.cart.user)
        self.assertEqual(cart_item.cart.id, guest_cart.id)

    def test_guest_cart_string_representation(self):
        request = self._create_guest_request(session_key=self.session_key)
        cart_service = CartService(request=request)

        self.assertTrue(str(cart_service).startswith("Cart Anonymous"))

    def test_guest_cart_with_mismatched_session_key(self):
        existing_cart = CartFactory(user=None, session_key="different-key")

        request = self._create_guest_request(
            cart_id=existing_cart.id, session_key=self.session_key
        )

        cart_service = CartService(request=request)
        cart = cart_service.get_or_create_cart()

        self.assertNotEqual(cart.id, existing_cart.id)
        self.assertEqual(cart.session_key, self.session_key)

    def test_guest_cart_no_headers_creates_new(self):
        request = self._create_guest_request()

        cart_service = CartService(request=request)
        cart = cart_service.get_or_create_cart()

        self.assertIsNotNone(cart)
        self.assertIsNotNone(cart.session_key)
        self.assertIsNone(cart.user)

    def test_guest_cart_transition_to_user(self):
        guest_cart = CartFactory(user=None, session_key=self.session_key)
        CartItemFactory(cart=guest_cart, product=self.product, quantity=2)

        user = UserAccountFactory(num_addresses=0)

        guest_request = self._create_guest_request(
            cart_id=guest_cart.id, session_key=self.session_key
        )
        guest_service = CartService(request=guest_request)
        self.assertEqual(guest_service.cart.items.count(), 1)

        user_request = self._create_request_with_headers(
            user=user, cart_id=guest_cart.id, session_key=self.session_key
        )

        user_service = CartService(request=user_request)

        self.assertEqual(user_service.cart.user, user)
        self.assertEqual(user_service.cart.items.count(), 1)
        self.assertEqual(user_service.cart.items.first().quantity, 2)

        self.assertFalse(Cart.objects.filter(id=guest_cart.id).exists())

    def _create_request_with_headers(
        self, user, cart_id=None, session_key=None
    ):
        request = self.factory.get("/")
        request.user = user

        if cart_id:
            request.META["HTTP_X_CART_ID"] = str(cart_id)
        if session_key:
            request.META["HTTP_X_SESSION_KEY"] = session_key

        request.session = {}
        return request
