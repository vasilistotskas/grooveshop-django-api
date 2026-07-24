"""C5/C6 — validate_cart_for_checkout rejects deactivated products."""

import pytest
from django.conf import settings
from django.test import TestCase
from djmoney.money import Money

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from order.services import OrderService
from product.factories.product import ProductFactory


pytestmark = pytest.mark.assert_english


class ValidateCartInactiveProductTestCase(TestCase):
    """Inactive products in cart must cause validate_cart_for_checkout to fail."""

    @classmethod
    def setUpTestData(cls):
        cls.active_product = ProductFactory.create(
            stock=10,
            active=True,
            price=Money("20.00", settings.DEFAULT_CURRENCY),
        )
        cls.active_product.set_current_language("en")
        cls.active_product.name = "Active Product"
        cls.active_product.save()

        cls.inactive_product = ProductFactory.create(
            stock=10,
            active=False,
            price=Money("20.00", settings.DEFAULT_CURRENCY),
        )
        cls.inactive_product.set_current_language("en")
        cls.inactive_product.name = "Inactive Product"
        cls.inactive_product.save()

    def test_inactive_product_fails_validation(self):
        cart = CartFactory.create(user=None, num_cart_items=0)
        CartItemFactory.create(
            cart=cart, product=self.inactive_product, quantity=1
        )

        result = OrderService.validate_cart_for_checkout(cart)

        self.assertFalse(result["valid"])
        self.assertTrue(len(result["errors"]) > 0)
        error_text = " ".join(str(e) for e in result["errors"])
        self.assertIn("no longer available", error_text)

    def test_active_product_passes_validation(self):
        cart = CartFactory.create(user=None, num_cart_items=0)
        CartItemFactory.create(
            cart=cart, product=self.active_product, quantity=1
        )

        result = OrderService.validate_cart_for_checkout(cart)

        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])

    def test_mixed_cart_with_inactive_product_fails(self):
        """Cart with one active and one inactive product must fail."""
        cart = CartFactory.create(user=None, num_cart_items=0)
        CartItemFactory.create(
            cart=cart, product=self.active_product, quantity=1
        )
        CartItemFactory.create(
            cart=cart, product=self.inactive_product, quantity=1
        )

        result = OrderService.validate_cart_for_checkout(cart)

        self.assertFalse(result["valid"])
        error_text = " ".join(str(e) for e in result["errors"])
        self.assertIn("no longer available", error_text)

    def test_product_name_uses_safe_translation_getter(self):
        """Error message must not raise AttributeError even without active
        translation context."""
        # product with no translations set for current language
        product_no_trans = ProductFactory.create(
            stock=10,
            active=False,
            price=Money("20.00", settings.DEFAULT_CURRENCY),
        )

        cart = CartFactory.create(user=None, num_cart_items=0)
        CartItemFactory.create(cart=cart, product=product_no_trans, quantity=1)

        # Must not raise AttributeError
        result = OrderService.validate_cart_for_checkout(cart)
        self.assertFalse(result["valid"])
