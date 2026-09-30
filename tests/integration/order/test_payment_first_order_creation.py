from unittest.mock import MagicMock, patch

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
from order.models.order import Order
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from tests.utils.shipping import enable_rate
from user.factories.account import UserAccountFactory

PAYMENT_INTENT_ID = "pi_test_123abc"


class TestPaymentFirstOrderCreation(APITestCase):
    """``POST /order`` with a Stripe payment intent (payment-first)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory(num_addresses=0)
        cls.pay_way = PayWayFactory(
            provider_code="stripe",
            settlement=PaySettlement.ONLINE,
            active=True,
        )
        cls.country = CountryFactory()
        cls.region = RegionFactory(country=cls.country)
        # A ShippingRate is per-country — this ad-hoc country has none
        # until this call.
        enable_rate(cls.country)

        cls.product1 = ProductFactory(
            active=True, stock=10, num_images=0, num_reviews=0
        )
        cls.product2 = ProductFactory(
            active=True, stock=5, num_images=0, num_reviews=0
        )
        cls.cart = CartFactory(user=cls.user)
        CartItemFactory(cart=cls.cart, product=cls.product1, quantity=2)
        CartItemFactory(cart=cls.cart, product=cls.product2, quantity=1)
        cls.guest_cart = CartFactory(user=None)
        CartItemFactory(cart=cls.guest_cart, product=cls.product1, quantity=1)

        cls.create_url = reverse("order-list")

    def setUp(self):
        super().setUp()
        # These tests run outside any tenant context (public schema),
        # where stripe_credentials() has no fallback at all — provide a
        # stand-in tenant key so the "stripe" pay-way is treated as
        # configured (this suite tests order creation, not Stripe
        # credential resolution).
        patcher = patch(
            "tenant.credentials.stripe_credentials",
            return_value={
                "secret_key": "sk_test_dummy_tenant_key",
                "publishable_key": "pk_test_dummy_tenant_key",
                "live_mode": False,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _order_data(self, **overrides):
        data = {
            "payment_intent_id": PAYMENT_INTENT_ID,
            "pay_way_id": self.pay_way.id,
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com",
            "street": "Main Street",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": self.country.alpha_2,
            "region_id": self.region.alpha,
            "phone": "+306900000000",
            "shipping_kind": "home_delivery",
        }
        data.update(overrides)
        return data

    def _post(self, data, cart=None):
        return self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str((cart or self.cart).uuid),
        )

    def _provider_confirming_payment(self):
        provider = MagicMock()
        provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {},
        )
        return provider

    @patch("order.payment.get_payment_provider")
    def test_creates_the_order_for_the_signed_in_shopper(
        self, mock_get_provider
    ):
        mock_get_provider.return_value = self._provider_confirming_payment()
        self.client.force_authenticate(user=self.user)

        response = self._post(self._order_data())

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.data
        )
        self.assertEqual(response.data["status"], OrderStatus.PENDING)
        self.assertEqual(response.data["payment_id"], PAYMENT_INTENT_ID)
        self.assertEqual(response.data["user"], self.user.id)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(str(order.uuid), response.data["uuid"])
        self.assertEqual(
            dict(order.items.values_list("product_id", "quantity")),
            {self.product1.id: 2, self.product2.id: 1},
        )
        mock_get_provider.return_value.get_payment_status.assert_called_with(
            PAYMENT_INTENT_ID
        )

    @patch("order.payment.get_payment_provider")
    def test_creates_a_guest_order_from_the_guest_cart(self, mock_get_provider):
        mock_get_provider.return_value = self._provider_confirming_payment()

        response = self._post(self._order_data(), cart=self.guest_cart)

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.data
        )
        self.assertIsNone(response.data["user"])
        self.assertEqual(response.data["payment_id"], PAYMENT_INTENT_ID)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(
            list(order.items.values_list("product_id", "quantity")),
            [(self.product1.id, 1)],
        )

    def test_intentless_online_order_is_refused(self):
        """Intent-less online orders are refused unless deductions
        (promotions / loyalty / gift cards) fully cover the total."""
        self.client.force_authenticate(user=self.user)
        data = self._order_data()
        del data["payment_intent_id"]

        response = self._post(data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["type"], "invalid_order_data")
        self.assertIn("payment_intent_id", response.data["field_errors"])

    def test_request_field_errors_are_reported_per_field(self):
        self.client.force_authenticate(user=self.user)
        for field, data in (
            ("first_name", self._order_data(first_name="")),
            ("email", self._order_data(email="invalid-email")),
            ("pay_way_id", self._order_data(pay_way_id=None)),
            ("pay_way_id", self._order_data(pay_way_id=99999)),
        ):
            with self.subTest(field=field, value=data[field]):
                response = self._post(data)

                self.assertEqual(
                    response.status_code, status.HTTP_400_BAD_REQUEST
                )
                self.assertIn(field, response.data)

        self.assertFalse(Order.objects.filter(user=self.user).exists())

    @patch("order.services.OrderService.create_order_from_cart")
    def test_insufficient_stock_error_maps_to_a_typed_400(
        self, mock_create_order
    ):
        mock_create_order.side_effect = InsufficientStockError(
            product_id=self.product1.id, available=5, requested=10
        )
        self.client.force_authenticate(user=self.user)

        response = self._post(self._order_data())

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        self.assertEqual(
            response.data["error"],
            {
                "type": "insufficient_stock",
                "product_id": self.product1.id,
                "available": 5,
                "requested": 10,
            },
        )

    @patch("order.services.OrderService.create_order_from_cart")
    def test_invalid_order_data_error_maps_to_a_typed_400(
        self, mock_create_order
    ):
        mock_create_order.side_effect = InvalidOrderDataError(
            "Invalid order data",
            field_errors={"product": ["Product not found"]},
        )
        self.client.force_authenticate(user=self.user)

        response = self._post(self._order_data())

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["type"], "invalid_order_data")
        self.assertEqual(
            response.data["field_errors"], {"product": ["Product not found"]}
        )

    @patch("order.services.OrderService.create_order_from_cart")
    def test_payment_not_found_error_maps_to_a_typed_400(
        self, mock_create_order
    ):
        mock_create_order.side_effect = PaymentNotFoundError(
            payment_id=PAYMENT_INTENT_ID
        )
        self.client.force_authenticate(user=self.user)

        response = self._post(self._order_data())

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        self.assertEqual(response.data["error"]["type"], "payment_not_found")
