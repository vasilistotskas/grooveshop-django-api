from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from cart.factories import CartFactory
from cart.factories import CartItemFactory
from cart.models import Cart
from cart.models import CartItem
from product.factories.product import ProductFactory
from product.models.product import Product
from user.factories.account import UserAccountFactory

User = get_user_model()


class CartModelTestCase(TestCase):
    cart: Cart = None
    user: User = None
    cart_item_1: CartItem = None
    cart_item_2: CartItem = None

    def setUp(self):
        products = ProductFactory.create_batch(2, num_images=0, num_reviews=0)
        product_1: Product = products[0]
        product_2: Product = products[1]

        self.user = UserAccountFactory(num_addresses=0)
        self.cart = CartFactory(user=self.user, num_cart_items=0)
        self.cart_item_1 = CartItemFactory(cart=self.cart, product=product_1, quantity=2)
        self.cart_item_2 = CartItemFactory(cart=self.cart, product=product_2, quantity=3)

    def test_fields(self):
        self.assertEqual(self.cart.user, self.user)
        self.assertEqual(self.cart.last_activity.date(), timezone.now().date())

    def test_str_representation(self):
        expected_str = f"Cart {self.user} - Items: {self.cart.total_items} - Total: {self.cart.total_price}"
        self.assertEqual(str(self.cart), expected_str)

    def test_get_items(self):
        self.assertEqual(self.cart.get_items().count(), 2)

    def test_total_price(self):
        self.cart_item_1.refresh_from_db()
        self.cart_item_2.refresh_from_db()

        expected_total_price = self.cart_item_1.total_price.amount + self.cart_item_2.total_price.amount
        self.assertEqual(self.cart.total_price.amount, expected_total_price)

    def test_total_discount_value(self):
        self.cart_item_1.refresh_from_db()
        self.cart_item_2.refresh_from_db()

        expected_total_discount = (
            self.cart_item_1.total_discount_value.amount + self.cart_item_2.total_discount_value.amount
        )
        self.assertEqual(self.cart.total_discount_value.amount, expected_total_discount)

    def test_total_vat_value(self):
        expected_total_vat = self.cart_item_1.vat_value.amount + self.cart_item_2.vat_value.amount
        self.assertEqual(self.cart.total_vat_value.amount, expected_total_vat)

    def test_total_items(self):
        expected_total_items = self.cart_item_1.quantity + self.cart_item_2.quantity
        self.assertEqual(self.cart.total_items, expected_total_items)

    def test_total_items_unique(self):
        self.assertEqual(self.cart.total_items_unique, 2)

    def test_refresh_last_activity(self):
        last_activity_before_refresh = self.cart.last_activity
        self.cart.refresh_last_activity()
        self.assertNotEqual(self.cart.last_activity, last_activity_before_refresh)

    def tearDown(self) -> None:
        CartItem.objects.all().delete()
        Cart.objects.all().delete()
        Product.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()
