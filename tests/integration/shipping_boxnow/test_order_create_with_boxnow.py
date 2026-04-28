"""Integration tests for order creation with BoxNow shipping method.

These tests POST to the order-list endpoint and assert that:
- BoxNowShipment row is created with PENDING_CREATION state.
- COD pay_way is rejected.
- Missing boxnow_locker_id is rejected.

The order-create endpoint is payment-first: it expects a Stripe
payment-intent to already exist. We mock the payment provider + cart and
shipping-address validators per the pattern in
``tests/integration/order/test_payment_first_order_creation.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from country.factories import CountryFactory
from order.enum.shipping_method import OrderShippingMethod
from order.enum.status import PaymentStatus
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.factories import BoxNowLockerFactory
from shipping_boxnow.models import BoxNowShipment
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestOrderCreateWithBoxNow(APITestCase):
    """Tests for order creation when shipping_method is BOX_NOW_LOCKER."""

    def setUp(self):
        super().setUp()
        # The BOXNOW_ENABLED Setting defaults to False (production
        # safety: hide the option until BoxNow activates the partner
        # account). Tests that exercise the box_now_locker path need
        # to flip it on; the row is auto-seeded by the conftest's
        # `_reseed_extra_settings` fixture.
        from extra_settings.models import Setting

        Setting.objects.filter(name="BOXNOW_ENABLED").update(value_bool=True)

        self.user = UserAccountFactory(num_addresses=0)
        self.country = CountryFactory(num_regions=0)
        self.region = RegionFactory(country=self.country)
        self.online_pay_way = PayWayFactory(
            provider_code="stripe",
            is_online_payment=True,
            requires_confirmation=False,
        )
        self.cod_pay_way = PayWayFactory(
            provider_code="cash",
            is_online_payment=False,
            requires_confirmation=False,
        )
        self.product = ProductFactory(
            active=True, stock=20, num_images=0, num_reviews=0
        )
        self.locker = BoxNowLockerFactory(external_id="4")

        self.cart = CartFactory(user=self.user)
        CartItemFactory(cart=self.cart, product=self.product, quantity=1)

        self.create_url = reverse("order-list")
        self.payment_intent_id = "pi_test_boxnow_123"

    def _build_payload(
        self,
        *,
        pay_way_id: int,
        include_locker_id: bool = True,
        locker_id: str = "4",
    ) -> dict:
        payload = {
            "payment_intent_id": self.payment_intent_id,
            "pay_way_id": pay_way_id,
            "first_name": "BoxNow",
            "last_name": "Tester",
            "email": "boxnow@test.com",
            "street": "Leoforos Pentelis",
            "street_number": "125",
            "city": "Chalandri",
            "zipcode": "15232",
            "country_id": self.country.alpha_2,
            "region_id": self.region.alpha,
            "phone": "+302100000000",
            "shipping_price": "2.50",
            "shipping_method": OrderShippingMethod.BOX_NOW_LOCKER.value,
            "boxnow_compartment_size": 1,
        }
        if include_locker_id:
            payload["boxnow_locker_id"] = locker_id
        return payload

    def _mock_payment_success(self, mock_get_payment_provider):
        provider = MagicMock()
        provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {},
        )
        mock_get_payment_provider.return_value = provider

    @patch("order.payment.get_payment_provider")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_create_order_with_boxnow_method_creates_shipment_row(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_get_payment_provider,
    ):
        mock_validate_cart.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }
        mock_validate_address.return_value = None
        self._mock_payment_success(mock_get_payment_provider)

        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.create_url,
            self._build_payload(pay_way_id=self.online_pay_way.id),
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        assert response.status_code == status.HTTP_201_CREATED, (
            f"Unexpected response: {response.json()}"
        )

        from order.models.order import Order

        order = Order.objects.latest("id")
        assert order.shipping_method == OrderShippingMethod.BOX_NOW_LOCKER.value

        shipment = BoxNowShipment.objects.get(order=order)
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION.value
        assert shipment.locker_external_id == "4"
        assert shipment.compartment_size == 1

    @patch("order.payment.get_payment_provider")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_create_order_with_boxnow_method_rejects_cod_payway(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_get_payment_provider,
    ):
        mock_validate_cart.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }
        mock_validate_address.return_value = None
        self._mock_payment_success(mock_get_payment_provider)

        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.create_url,
            self._build_payload(pay_way_id=self.cod_pay_way.id),
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body_str = str(response.json()).lower()
        assert (
            "pay_way" in body_str or "cash" in body_str or "online" in body_str
        )

    @patch("order.payment.get_payment_provider")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_create_order_with_boxnow_method_requires_locker_id(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_get_payment_provider,
    ):
        mock_validate_cart.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }
        mock_validate_address.return_value = None
        self._mock_payment_success(mock_get_payment_provider)

        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.create_url,
            self._build_payload(
                pay_way_id=self.online_pay_way.id, include_locker_id=False
            ),
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body_str = str(response.json()).lower()
        assert "locker" in body_str or "boxnow" in body_str

    @patch("order.payment.get_payment_provider")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_create_order_rejected_when_boxnow_disabled_globally(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_get_payment_provider,
    ):
        """When the admin flips BOXNOW_ENABLED to False the API must
        reject ``box_now_locker`` orders even if the request is
        otherwise valid — defends against a stale frontend cache that
        still surfaces the option after admin hides it."""
        from extra_settings.models import Setting

        Setting.objects.filter(name="BOXNOW_ENABLED").update(value_bool=False)

        mock_validate_cart.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }
        mock_validate_address.return_value = None
        self._mock_payment_success(mock_get_payment_provider)

        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.create_url,
            self._build_payload(pay_way_id=self.online_pay_way.id),
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body_str = str(response.json()).lower()
        assert "shipping" in body_str or "unavailable" in body_str
