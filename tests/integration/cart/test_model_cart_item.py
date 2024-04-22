from django.test import TestCase

from cart.models import Cart
from cart.models import CartItem
from product.models.product import Product


class CartItemModelTestCase(TestCase):
    cart_item: CartItem = None
    cart: Cart = None
    product: Product = None

    def setUp(self):
        self.cart = Cart.objects.create()
        self.product = Product.objects.create(
            name="Product 1",
            slug="product_one",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=5.00,
            view_count=0,
            weight=0.00,
        )
        self.cart_item = CartItem.objects.create(
            cart=self.cart, product=self.product, quantity=3
        )

    def test_fields(self):
        self.assertEqual(self.cart_item.cart, self.cart)
        self.assertEqual(self.cart_item.product, self.product)
        self.assertEqual(self.cart_item.quantity, 3)

    def test_str_representation(self):
        product_name = self.product.safe_translation_getter("name", any_language=True)
        expected_str = f"CartItem {self.cart_item.id} in Cart {self.cart_item.cart.id}: {product_name} x {self.cart_item.quantity}"
        self.assertEqual(str(self.cart_item), expected_str)

    def test_price(self):
        self.assertEqual(self.cart_item.price, self.product.price)

    def test_final_price(self):
        self.assertEqual(self.cart_item.final_price, self.product.final_price)

    def test_discount_value(self):
        self.assertEqual(self.cart_item.discount_value, self.product.discount_value)

    def test_price_save_percent(self):
        self.assertEqual(
            self.cart_item.price_save_percent, self.product.price_save_percent
        )

    def test_discount_percent(self):
        self.assertEqual(self.cart_item.discount_percent, self.product.discount_percent)

    def test_vat_percent(self):
        self.assertEqual(self.cart_item.vat_percent, self.product.vat_percent)

    def test_vat_value(self):
        self.assertEqual(self.cart_item.vat_value, self.product.vat_value)

    def test_total_price(self):
        expected_total_price = self.cart_item.quantity * self.product.final_price
        self.assertEqual(self.cart_item.total_price, expected_total_price)

    def test_total_discount_value(self):
        expected_total_discount = self.cart_item.quantity * self.product.discount_value
        self.assertEqual(self.cart_item.total_discount_value, expected_total_discount)

    def test_update_quantity(self):
        new_quantity = 5
        self.cart_item.update_quantity(new_quantity)
        self.assertEqual(self.cart_item.quantity, new_quantity)

    def test_cart_item_ordering(self):
        product_2 = Product.objects.create(
            name="Product 2",
            slug="product_two",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=5.00,
            view_count=0,
            weight=0.00,
        )
        cart_item_2 = CartItem.objects.create(
            cart=self.cart, product=product_2, quantity=3
        )
        self.assertLess(self.cart_item.id, cart_item_2.id)

    def tearDown(self) -> None:
        super().tearDown()
        self.cart_item.delete()
        self.cart.delete()
        self.product.delete()
