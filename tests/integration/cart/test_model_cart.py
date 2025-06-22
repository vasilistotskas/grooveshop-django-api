import uuid
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from cart.models import Cart, CartItem
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

if TYPE_CHECKING:
    from product.models.product import Product

User = get_user_model()


class CartModelTestCase(TestCase):
    def setUp(self):
        products = ProductFactory.create_batch(2, num_images=0, num_reviews=0)
        product_1: Product = products[0]
        product_2: Product = products[1]

        self.user = UserAccountFactory(num_addresses=0)
        self.cart = CartFactory(user=self.user, num_cart_items=0)
        self.cart_item_1 = CartItemFactory(
            cart=self.cart, product=product_1, quantity=2
        )
        self.cart_item_2 = CartItemFactory(
            cart=self.cart, product=product_2, quantity=3
        )

    def test_fields(self):
        self.assertEqual(self.cart.user, self.user)
        self.assertEqual(self.cart.last_activity.date(), timezone.now().date())

    def test_str_representation(self):
        expected_str = f"Cart for {self.user} - Items: {self.cart.total_items} - Total: {self.cart.total_price}"
        self.assertEqual(str(self.cart), expected_str)

    def test_get_items(self):
        self.assertEqual(self.cart.get_items().count(), 2)

    def test_total_price(self):
        self.cart_item_1.refresh_from_db()
        self.cart_item_2.refresh_from_db()

        expected_total_price = (
            self.cart_item_1.total_price.amount
            + self.cart_item_2.total_price.amount
        )
        self.assertEqual(self.cart.total_price.amount, expected_total_price)

    def test_total_discount_value(self):
        self.cart_item_1.refresh_from_db()
        self.cart_item_2.refresh_from_db()

        expected_total_discount = (
            self.cart_item_1.total_discount_value.amount
            + self.cart_item_2.total_discount_value.amount
        )
        self.assertEqual(
            self.cart.total_discount_value.amount, expected_total_discount
        )

    def test_total_vat_value(self):
        expected_total_vat = (
            self.cart_item_1.vat_value.amount
            + self.cart_item_2.vat_value.amount
        )
        self.assertEqual(self.cart.total_vat_value.amount, expected_total_vat)

    def test_total_items(self):
        expected_total_items = (
            self.cart_item_1.quantity + self.cart_item_2.quantity
        )
        self.assertEqual(self.cart.total_items, expected_total_items)

    def test_total_items_unique(self):
        self.assertEqual(self.cart.total_items_unique, 2)

    def test_refresh_last_activity(self):
        last_activity_before_refresh = self.cart.last_activity
        self.cart.refresh_last_activity()
        self.assertNotEqual(
            self.cart.last_activity, last_activity_before_refresh
        )


class GuestCartModelTestCase(TestCase):
    def setUp(self):
        products = ProductFactory.create_batch(2, num_images=0, num_reviews=0)
        product_1: Product = products[0]
        product_2: Product = products[1]

        self.session_key = str(uuid.uuid4())
        self.cart = CartFactory(
            user=None, session_key=self.session_key, num_cart_items=0
        )
        self.cart_item_1 = CartItemFactory(
            cart=self.cart, product=product_1, quantity=2
        )
        self.cart_item_2 = CartItemFactory(
            cart=self.cart, product=product_2, quantity=3
        )

    def test_fields(self):
        self.assertIsNone(self.cart.user)
        self.assertEqual(self.cart.session_key, self.session_key)
        self.assertEqual(self.cart.last_activity.date(), timezone.now().date())

    def test_str_representation(self):
        expected_str = f"Guest Cart ({self.session_key[:8]}...) - Items: {self.cart.total_items} - Total: {self.cart.total_price}"
        self.assertEqual(str(self.cart), expected_str)

    def test_get_items(self):
        self.assertEqual(self.cart.get_items().count(), 2)

    def test_total_items(self):
        expected_total_items = (
            self.cart_item_1.quantity + self.cart_item_2.quantity
        )
        self.assertEqual(self.cart.total_items, expected_total_items)

    def test_total_items_unique(self):
        self.assertEqual(self.cart.total_items_unique, 2)

    def test_create_guest_cart(self):
        session_key = str(uuid.uuid4())
        cart = Cart.objects.create(session_key=session_key)
        self.assertIsNotNone(cart)
        self.assertIsNone(cart.user)
        self.assertEqual(cart.session_key, session_key)

    def test_add_item_to_guest_cart(self):
        session_key = str(uuid.uuid4())
        cart = Cart.objects.create(session_key=session_key)
        product = ProductFactory(num_images=0, num_reviews=0)
        cart_item = CartItem.objects.create(
            cart=cart, product=product, quantity=3
        )
        self.assertEqual(cart_item.quantity, 3)
        self.assertEqual(cart_item.product, product)
        self.assertIsNone(cart_item.cart.user)
        self.assertEqual(cart_item.cart.session_key, session_key)
