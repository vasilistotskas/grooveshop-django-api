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
        # ``ShippingProvider.is_active`` is the master switch in
        # checkout — defaults to False from the seed migration so a
        # fresh deploy hides the option until an admin activates it.
        # Tests that exercise the box_now_locker path need to flip it
        # on. ``boxnow`` is auto-seeded by ``shipping/migrations/
        # 0002_seed_providers.py``.
        from shipping.models import ShippingProvider

        ShippingProvider.objects.filter(code="boxnow").update(is_active=True)

        self.user = UserAccountFactory(num_addresses=0)
        self.country = CountryFactory(num_regions=0)
        self.region = RegionFactory(country=self.country)
        self.online_pay_way = PayWayFactory(
            provider_code="stripe",
            is_online_payment=True,
            requires_confirmation=False,
            active=True,
        )
        self.cod_pay_way = PayWayFactory(
            provider_code="cash",
            is_online_payment=False,
            requires_confirmation=False,
            active=True,
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
            # Registry-driven dispatch — the order serializer resolves
            # the carrier adapter from this explicit ``(provider, kind)``
            # pair (see ``order/services._resolve_shipping_provider``).
            "shipping_provider_code": "boxnow",
            "shipping_kind": "pickup_point",
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
            HTTP_X_CART_ID=str(self.cart.uuid),
        )

        assert response.status_code == status.HTTP_201_CREATED, (
            f"Unexpected response: {response.json()}"
        )

        from order.models.order import Order

        order = Order.objects.latest("id")
        assert order.shipping_provider is not None
        assert order.shipping_provider.code == "boxnow"
        assert order.shipping_kind == "pickup_point"

        shipment = BoxNowShipment.objects.get(order=order)
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION.value
        assert shipment.locker_external_id == "4"
        assert shipment.compartment_size == 1

    @patch("order.payment.get_payment_provider")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_create_order_with_boxnow_method_accepts_cod_payway(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_get_payment_provider,
    ):
        # BoxNow PAY ON THE GO supports cash-on-delivery on lockers:
        # the shipment row must be created with payment_mode=COD so the
        # voucher prints "COD". ``amount_to_be_collected`` is set by
        # ``BoxNowService.create_shipment_for_order`` once items are
        # persisted (order.total_price is 0 at row-creation time).
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
            HTTP_X_CART_ID=str(self.cart.uuid),
        )

        assert response.status_code == status.HTTP_201_CREATED, (
            f"Unexpected response: {response.json()}"
        )

        from order.models.order import Order
        from shipping_boxnow.enum.payment_mode import BoxNowPaymentMode

        order = Order.objects.latest("id")
        shipment = BoxNowShipment.objects.get(order=order)
        assert shipment.payment_mode == BoxNowPaymentMode.COD.value

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
            HTTP_X_CART_ID=str(self.cart.uuid),
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
        """When the admin flips ``ShippingProvider.is_active`` to
        False the API must reject ``box_now_locker`` orders even if
        the request is otherwise valid — defends against a stale
        frontend cache that still surfaces the option after admin
        hides it."""
        from shipping.models import ShippingProvider

        ShippingProvider.objects.filter(code="boxnow").update(is_active=False)

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
            HTTP_X_CART_ID=str(self.cart.uuid),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body_str = str(response.json()).lower()
        assert "shipping" in body_str or "unavailable" in body_str
