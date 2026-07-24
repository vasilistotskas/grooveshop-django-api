from decimal import Decimal

from django.test import TestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from product.factories.product import ProductFactory
from vat.models import Vat


class CartItemModelTestCase(TestCase):
    def setUp(self):
        self.cart = CartFactory(num_cart_items=0)
        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.cart_item = CartItemFactory(
            cart=self.cart, product=self.product, quantity=3
        )

    def test_fields(self):
        self.assertEqual(self.cart_item.cart, self.cart)
        self.assertEqual(self.cart_item.product, self.product)
        self.assertEqual(self.cart_item.quantity, 3)

    def test_str_representation(self):
        product_name = self.product.safe_translation_getter(
            "name", any_language=True
        )
        expected_str = (
            f"CartItem {self.cart_item.id} in Cart {self.cart_item.cart.id}: "
            f"{product_name} x {self.cart_item.quantity}"
        )
        self.assertEqual(str(self.cart_item), expected_str)

    def test_price(self):
        self.assertEqual(self.cart_item.price, self.product.price)

    def test_final_price(self):
        self.assertEqual(self.cart_item.final_price, self.product.final_price)

    def test_discount_value(self):
        self.assertEqual(
            self.cart_item.discount_value, self.product.discount_value
        )

    def test_price_save_percent(self):
        self.assertEqual(
            self.cart_item.price_save_percent, self.product.price_save_percent
        )

    def test_discount_percent(self):
        self.assertEqual(
            self.cart_item.discount_percent, self.product.discount_percent
        )

    def test_vat_percent(self):
        self.assertEqual(self.cart_item.vat_percent, self.product.vat_percent)

    def test_vat_value(self):
        self.assertEqual(self.cart_item.vat_value, self.product.vat_value)

    def test_total_price(self):
        expected_total_price = (
            self.cart_item.quantity * self.product.final_price
        )
        self.assertEqual(self.cart_item.total_price, expected_total_price)

    def test_total_discount_value(self):
        expected_total_discount = (
            self.cart_item.quantity * self.product.discount_value
        )
        self.assertEqual(
            self.cart_item.total_discount_value, expected_total_discount
        )

    def test_price_at_add_round_trips_without_drift(self):
        # price_at_add snapshots final_price and round-trips through a
        # numeric(11,2) column. With sub-cent final_price values the
        # stored snapshot differed from the live value, firing phantom
        # "Cart price drift at checkout" warnings (16.12 × 24% VAT
        # = 3.8688 → final 19.9888 vs stored 19.99).
        vat_24, _ = Vat.objects.get_or_create(value=Decimal("24"))
        product = ProductFactory(
            num_images=0,
            num_reviews=0,
            vat=vat_24,
            price=Decimal("16.12"),
            discount_percent=Decimal("0.0"),
        )
        cart_item = CartItemFactory(cart=self.cart, product=product, quantity=1)
        cart_item.refresh_from_db()

        self.assertEqual(cart_item.price_at_add, product.final_price)

    def test_update_quantity(self):
        new_quantity = 5
        self.cart_item.update_quantity(new_quantity)
        self.assertEqual(self.cart_item.quantity, new_quantity)

    def test_cart_item_ordering(self):
        product_2 = ProductFactory(num_images=0, num_reviews=0)
        cart_item_2 = CartItemFactory(
            cart=self.cart, product=product_2, quantity=3
        )
        self.assertLess(self.cart_item.id, cart_item_2.id)
