from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from country.factories import CountryFactory
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import (
    InsufficientStockError,
    InvalidOrderDataError,
    PaymentNotFoundError,
)
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory

User = get_user_model()


@pytest.mark.django_db
class TestPaymentFirstOrderCreation(APITestCase):
    """Test cases for payment-first order creation flow."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()

        # Create users
        self.user = UserAccountFactory()
        self.guest_user = None

        # Create payment method (online payment for Stripe)
        self.pay_way = PayWayFactory(
            provider_code="stripe",
            is_online_payment=True,
            requires_confirmation=False,
        )

        # Create location data
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)

        # Create products with stock
        self.product1 = ProductFactory(
            active=True, stock=10, num_images=0, num_reviews=0
        )
        self.product2 = ProductFactory(
            active=True, stock=5, num_images=0, num_reviews=0
        )

        # Create cart for authenticated user
        self.cart = CartFactory(user=self.user)
        self.cart_item1 = CartItemFactory(
            cart=self.cart, product=self.product1, quantity=2
        )
        self.cart_item2 = CartItemFactory(
            cart=self.cart, product=self.product2, quantity=1
        )

        # Create guest cart
        self.guest_cart = CartFactory(user=None)
        self.guest_cart_item = CartItemFactory(
            cart=self.guest_cart, product=self.product1, quantity=1
        )

        # API endpoint
        self.create_url = reverse("order-list")

        # Valid payment intent ID (mocked)
        self.payment_intent_id = "pi_test_123abc"

    def _get_valid_order_data(self, payment_intent_id=None):
        """Helper to generate valid order creation data."""
        return {
            "payment_intent_id": payment_intent_id or self.payment_intent_id,
            "pay_way_id": self.pay_way.id,
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com",
            "street": "Main Street",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": self.country.alpha_2,  # Country uses alpha_2 as PK
            "region_id": self.region.alpha,  # Region uses alpha as PK
            "phone": "+30123456789",
            "notes": "Test order",
        }

    @patch("order.payment.get_payment_provider")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_create_order_with_valid_payment_intent(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_get_payment_provider,
    ):
        """Test successful order creation with valid payment intent."""
        # Setup mocks
        mock_validate_cart.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }
        mock_validate_address.return_value = None

        # Mock payment provider to return successful payment status
        mock_provider = MagicMock()
        mock_provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {"amount": "100.00", "currency": "EUR"},
        )
        mock_get_payment_provider.return_value = mock_provider

        # Authenticate and make request with X-Cart-Id header
        self.client.force_authenticate(user=self.user)
        data = self._get_valid_order_data()
        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        # Assertions
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("id", response.data)
        self.assertIn("uuid", response.data)
        self.assertEqual(response.data["status"], OrderStatus.PENDING.value)
        self.assertEqual(response.data["payment_id"], self.payment_intent_id)
        self.assertEqual(response.data["user"], self.user.id)

        # Validate cart is called (may be called multiple times - view and service)
        self.assertTrue(mock_validate_cart.called)
        self.assertTrue(mock_validate_address.called)
        self.assertTrue(mock_get_payment_provider.called)

    def test_create_order_without_payment_intent_id(self):
        """Test that order creation fails without payment_intent_id."""
        self.client.force_authenticate(user=self.user)
        data = self._get_valid_order_data()
        del data["payment_intent_id"]

        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("payment_intent_id", response.data)

    @patch("order.services.OrderService.validate_cart_for_checkout")
    def test_create_order_with_invalid_cart(self, mock_validate_cart):
        """Test that order creation fails when cart validation fails."""
        mock_validate_cart.return_value = {
            "valid": False,
            "errors": ["Product out of stock", "Price mismatch"],
            "warnings": [],
        }

        self.client.force_authenticate(user=self.user)
        data = self._get_valid_order_data()

        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cart", response.data)

    def test_create_order_with_missing_shipping_address_fields(self):
        """Test that order creation fails with incomplete shipping address."""
        self.client.force_authenticate(user=self.user)
        data = self._get_valid_order_data()
        del data["first_name"]  # Remove required field

        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_order_with_invalid_email(self):
        """Test that order creation fails with invalid email format."""
        self.client.force_authenticate(user=self.user)
        data = self._get_valid_order_data()
        data["email"] = "invalid-email"  # Invalid email format

        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_order_without_pay_way_id(self):
        """Test that order creation fails without payment method."""
        self.client.force_authenticate(user=self.user)
        data = self._get_valid_order_data()
        del data["pay_way_id"]

        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("pay_way_id", response.data)

    def test_create_order_with_invalid_pay_way_id(self):
        """Test that order creation fails with non-existent payment method."""
        self.client.force_authenticate(user=self.user)
        data = self._get_valid_order_data()
        data["pay_way_id"] = 99999  # Non-existent ID

        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("pay_way_id", response.data)

    @patch("order.services.OrderService.create_order_from_cart")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_create_order_handles_insufficient_stock_error(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_create_order,
    ):
        """Test that InsufficientStockError is handled correctly."""
        mock_validate_cart.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }
        mock_validate_address.return_value = None
        mock_create_order.side_effect = InsufficientStockError(
            product_id=self.product1.id, available=5, requested=10
        )

        self.client.force_authenticate(user=self.user)
        data = self._get_valid_order_data()

        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        self.assertIn("error", response.data)
        self.assertEqual(response.data["error"]["type"], "insufficient_stock")
        self.assertEqual(response.data["error"]["product_id"], self.product1.id)
        self.assertEqual(response.data["error"]["available"], 5)
        self.assertEqual(response.data["error"]["requested"], 10)

    @patch("order.services.OrderService.create_order_from_cart")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_create_order_handles_invalid_order_data_error(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_create_order,
    ):
        """Test that InvalidOrderDataError is handled correctly."""
        mock_validate_cart.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }
        mock_validate_address.return_value = None
        mock_create_order.side_effect = InvalidOrderDataError(
            "Invalid order data",
            field_errors={"product": ["Product not found"]},
        )

        self.client.force_authenticate(user=self.user)
        data = self._get_valid_order_data()

        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        self.assertIn("error", response.data)
        self.assertEqual(response.data["error"]["type"], "invalid_order_data")
        self.assertIn("field_errors", response.data)

    @patch("order.services.OrderService.create_order_from_cart")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_create_order_handles_payment_not_found_error(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_create_order,
    ):
        """Test that PaymentNotFoundError is handled correctly."""
        mock_validate_cart.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }
        mock_validate_address.return_value = None
        mock_create_order.side_effect = PaymentNotFoundError(
            payment_id=self.payment_intent_id
        )

        self.client.force_authenticate(user=self.user)
        data = self._get_valid_order_data()

        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        self.assertIn("error", response.data)
        self.assertEqual(response.data["error"]["type"], "payment_not_found")
        self.assertEqual(
            response.data["error"]["payment_id"], self.payment_intent_id
        )

    @patch("order.payment.get_payment_provider")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_create_guest_order_with_valid_data(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_get_payment_provider,
    ):
        """Test successful guest order creation."""
        mock_validate_cart.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }
        mock_validate_address.return_value = None

        # Mock payment provider to return successful payment status
        mock_provider = MagicMock()
        mock_provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {"amount": "100.00", "currency": "EUR"},
        )
        mock_get_payment_provider.return_value = mock_provider

        # Make request without authentication (guest) with X-Cart-Id header
        data = self._get_valid_order_data()
        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.guest_cart.id),
        )

        # Assertions
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("id", response.data)
        self.assertIn("uuid", response.data)
        self.assertEqual(response.data["status"], OrderStatus.PENDING.value)
        self.assertEqual(response.data["payment_id"], self.payment_intent_id)
        self.assertIsNone(response.data["user"])  # Guest order has no user

    @patch("order.payment.get_payment_provider")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_create_order_with_cart_integer_id(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_get_payment_provider,
    ):
        """Test order creation using cart integer ID in X-Cart-Id header."""
        mock_validate_cart.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }
        mock_validate_address.return_value = None

        # Mock payment provider to return successful payment status
        mock_provider = MagicMock()
        mock_provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {"amount": "100.00", "currency": "EUR"},
        )
        mock_get_payment_provider.return_value = mock_provider

        # Authenticate and make request with cart integer ID in header
        self.client.force_authenticate(user=self.user)
        data = self._get_valid_order_data()
        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        # Assertions
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("id", response.data)
        self.assertIn("uuid", response.data)
        self.assertEqual(response.data["status"], OrderStatus.PENDING.value)
        self.assertEqual(response.data["payment_id"], self.payment_intent_id)
        self.assertEqual(response.data["user"], self.user.id)
