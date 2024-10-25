from django.contrib.auth import get_user_model
from django.core.handlers.wsgi import WSGIRequest
from django.test import RequestFactory
from django.test import TestCase

from cart.factories import CartFactory
from cart.factories import CartItemFactory
from cart.models import Cart
from cart.models import CartItem
from cart.service import CartService
from cart.service import CartServiceInitException
from cart.service import InvalidProcessCartOptionException
from cart.service import ProcessCartOption
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
        cart_service = CartService(cart_id=self.cart.pk)
        cart_service.create_cart_item(self.product, 2)
        cart_item = cart_service.get_cart_item(self.product.pk)
        self.assertEqual(cart_item.product, self.product)

    def test_create_cart_item(self):
        CartItem.objects.all().delete()
        cart_service = CartService(cart_id=self.cart.pk)
        cart_item = cart_service.create_cart_item(self.product, 3)
        self.assertEqual(cart_item.quantity, 3)
        self.assertEqual(CartItem.objects.count(), 1)

    def test_update_cart_item(self):
        cart_service = CartService(cart_id=self.cart.pk)
        cart_service.create_cart_item(self.product, 2)
        cart_item = cart_service.update_cart_item(self.product.pk, 5)
        self.assertEqual(cart_item.quantity, 5)

    def test_delete_cart_item(self):
        cart_service = CartService(cart_id=self.cart.pk)
        cart_service.create_cart_item(self.product, 2)
        cart_service.delete_cart_item(self.product.pk)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_process_cart_merge(self):
        cart_service = CartService(cart_id=self.cart.pk)
        pre_login_cart = CartFactory(user=None, num_cart_items=0)
        cart_service.create_cart_item(self.product, 2)
        cache_instance.set(str(self.user.id), pre_login_cart.id, 3600)

        cart_service.process_cart(self.request, option=ProcessCartOption.MERGE)

        self.assertEqual(self.cart.items.count(), 1)
        self.assertEqual(self.cart.items.first().quantity, 2)

    def test_process_cart_merge_with_pre_login_cart_in_session(self):
        cart_service = CartService(cart_id=self.cart.pk)
        pre_login_cart = CartFactory(user=None, num_cart_items=0)
        cart_service.create_cart_item(self.product, 2)
        self.request.session = {"pre_log_in_cart_id": pre_login_cart.id}

        cart_service.process_cart(self.request, option=ProcessCartOption.MERGE)

        self.assertEqual(self.cart.items.count(), 1)
        self.assertEqual(self.cart.items.first().quantity, 2)

    def test_process_cart_keep(self):
        cart_service = CartService(cart_id=self.cart.pk)
        pre_login_cart = CartFactory(user=None, num_cart_items=0)
        cart_service.create_cart_item(self.product, 3)
        cache_instance.set(str(self.user.id), pre_login_cart.id, 3600)

        cart_service.process_cart(self.request, option=ProcessCartOption.KEEP)

        self.assertEqual(self.cart.items.count(), 1)
        self.assertEqual(self.cart.items.first().quantity, 3)

    def test_process_cart_clean(self):
        cart_service = CartService(cart_id=self.cart.pk)
        cart_service.create_cart_item(self.product, 2)

        cart_service.process_cart(self.request, option=ProcessCartOption.CLEAN)

        self.assertEqual(self.cart.items.count(), 0)

    def test_process_cart_invalid_option(self):
        cart_service = CartService(cart_id=self.cart.pk)
        with self.assertRaises(InvalidProcessCartOptionException):
            cart_service.process_cart(self.request, option="invalid_option")

    def test_get_cart_by_id_valid(self):
        cart_service = CartService(cart_id=self.cart.pk)
        cart_id = self.cart.id

        cart = cart_service.get_cart_by_id(cart_id)

        self.assertEqual(cart, self.cart)

    def test_get_cart_by_id_invalid(self):
        cart_service = CartService(cart_id=self.cart.pk)
        cart_id = self.cart.id + 1

        cart = cart_service.get_cart_by_id(cart_id)

        self.assertIsNone(cart)

    def test_cart_service_string_representation(self):
        cart_service = CartService(cart_id=self.cart.pk)
        self.assertEqual(str(cart_service), f"Cart {self.user}")

    def test_cart_service_length(self):
        cart_service = CartService(cart_id=self.cart.pk)
        cart_service.create_cart_item(self.product, 3)

        self.assertEqual(len(cart_service), 3)

    def test_cart_service_iteration(self):
        cart_service = CartService(cart_id=self.cart.pk)
        cart_service.create_cart_item(self.product, 2)

        cart_items = list(cart_service)

        self.assertEqual(len(cart_items), 1)
        self.assertEqual(cart_items[0].product, self.product)

    def test_cart_service_add_more_items(self):
        cart_service = CartService(cart_id=self.cart.pk)

        product = ProductFactory(num_images=0, num_reviews=0)

        cart_service.create_cart_item(self.product, 3)
        cart_service.create_cart_item(product, 4)

        self.assertEqual(len(cart_service), 7)
        self.assertEqual(self.cart.items.count(), 2)

    def test_cart_service_remove_item(self):
        cart_service = CartService(cart_id=self.cart.pk)
        cart_service.create_cart_item(self.product, 2)

        cart_service.delete_cart_item(self.product.pk)

        self.assertEqual(len(cart_service), 0)
        self.assertEqual(self.cart.items.count(), 0)

    def test_init_without_cart_id_and_request(self):
        with self.assertRaises(CartServiceInitException):
            CartService(cart_id=None, request=None)

    def test_merge_carts_with_items_in_both_carts(self):
        cart_service = CartService(cart_id=self.cart.pk)

        pre_login_cart = CartFactory(user=None, num_cart_items=0)
        pre_login_product = ProductFactory(num_images=0, num_reviews=0)
        CartItemFactory(cart=pre_login_cart, product=pre_login_product, quantity=1)
        cart_service.create_cart_item(self.product, 2)

        self.request.session = {"pre_log_in_cart_id": pre_login_cart.id}

        cart_service.process_cart(self.request, option=ProcessCartOption.MERGE)

        self.assertEqual(self.cart.items.count(), 2)
        self.assertCountEqual(
            [item.product for item in self.cart.items.all()],
            [self.product, pre_login_product],
        )
