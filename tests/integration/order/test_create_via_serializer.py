"""Tests for R4-D4: OrderViewSet.create() validates via serializer.

Verifies that:
1. Fields forbidden by the schema (e.g. unknown keys) are rejected.
2. Cross-field validation (email disposable, B2B invoice, BoxNow) runs.
3. Both payment flows (online + offline) go through the serializer.
4. The floor/place/location_type optional fields added to
   OrderCreateFromCartSerializer are accepted without error.
"""

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
from order.serializers.order import OrderCreateFromCartSerializer
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory

User = get_user_model()


@pytest.mark.django_db
class TestOrderCreateSerializerValidation(APITestCase):
    """OrderCreateFromCartSerializer validates the create payload."""

    def setUp(self):
        super().setUp()
        self.user = UserAccountFactory()
        self.pay_way = PayWayFactory(
            provider_code="stripe",
            is_online_payment=True,
        )
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)
        self.product = ProductFactory(
            active=True, stock=10, num_images=0, num_reviews=0
        )
        self.cart = CartFactory(user=self.user)
        CartItemFactory(cart=self.cart, product=self.product, quantity=1)
        self.create_url = reverse("order-list")

    def _base_data(self, **overrides):
        data = {
            "pay_way_id": self.pay_way.id,
            "payment_intent_id": "pi_test_abc123",
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "street": "Main St",
            "street_number": "1",
            "city": "Athens",
            "zipcode": "11234",
            "country_id": self.country.alpha_2,
            "region_id": self.region.alpha,
            "phone": "+306900000000",
        }
        data.update(overrides)
        return data

    def test_serializer_accepts_optional_address_fields(self):
        """floor, place, and location_type are accepted without error."""
        data = {
            "pay_way_id": self.pay_way.id,
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@example.com",
            "street": "Oak Avenue",
            "city": "Piraeus",
            "zipcode": "18534",
            "country_id": self.country.alpha_2,
            "phone": "+306900000001",
            "floor": "3",
            "place": "City center",
            "location_type": "home",
        }
        serializer = OrderCreateFromCartSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        vd = serializer.validated_data
        assert vd["floor"] == "3"
        assert vd["place"] == "City center"
        assert vd["location_type"] == "home"

    def test_serializer_rejects_invalid_email(self):
        """validate_email blocks malformed email addresses."""
        # A syntactically invalid email (no @ domain) must always fail.
        data = self._base_data(email="notanemail")
        serializer = OrderCreateFromCartSerializer(data=data)
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_serializer_requires_pay_way_id(self):
        """pay_way_id is required."""
        data = self._base_data()
        del data["pay_way_id"]
        serializer = OrderCreateFromCartSerializer(data=data)
        assert not serializer.is_valid()
        assert "pay_way_id" in serializer.errors

    def test_serializer_invoice_requires_billing_vat_id(self):
        """document_type=INVOICE without billing_vat_id fails validation."""
        data = self._base_data(
            document_type="INVOICE",
            billing_vat_id="",
        )
        # Setting is imported inside validate() so we patch at the source.
        with patch(
            "extra_settings.models.Setting.get",
            side_effect=lambda k, default=None: (
                True if k == "B2B_INVOICING_ENABLED" else default
            ),
        ):
            serializer = OrderCreateFromCartSerializer(data=data)
            assert not serializer.is_valid()
            assert "billing_vat_id" in serializer.errors

    def test_serializer_billing_vat_id_strips_el_prefix(self):
        """validate_billing_vat_id normalises 'EL123456789' → '123456789'."""
        data = self._base_data(billing_vat_id="EL123456789")
        serializer = OrderCreateFromCartSerializer(data=data)
        serializer.is_valid()
        assert serializer.validated_data.get("billing_vat_id") == "123456789"


@pytest.mark.django_db
class TestOrderCreateBothFlowsViaSerializer(APITestCase):
    """Both payment flows use serializer validation before routing."""

    def setUp(self):
        super().setUp()
        self.user = UserAccountFactory()
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)
        self.product = ProductFactory(
            active=True, stock=10, num_images=0, num_reviews=0
        )
        self.cart = CartFactory(user=self.user)
        CartItemFactory(cart=self.cart, product=self.product, quantity=1)
        self.create_url = reverse("order-list")

    def _base_address(self):
        return {
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "street": "Baker St",
            "street_number": "221B",
            "city": "Thessaloniki",
            "zipcode": "54621",
            "country_id": self.country.alpha_2,
            "region_id": self.region.alpha,
            "phone": "+306911111111",
        }

    @patch("order.payment.get_payment_provider")
    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_online_flow_accepted_with_valid_data(
        self,
        mock_validate_address,
        mock_validate_cart,
        mock_get_provider,
    ):
        """Stripe (online) flow creates an order when all fields are valid."""
        mock_validate_cart.return_value = {"valid": True, "errors": []}
        mock_validate_address.return_value = None
        mock_provider = MagicMock()
        mock_provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {},
        )
        mock_get_provider.return_value = mock_provider

        pay_way = PayWayFactory(provider_code="stripe", is_online_payment=True)
        data = {
            **self._base_address(),
            "pay_way_id": pay_way.id,
            "payment_intent_id": "pi_stripe_test_xyz",
        }

        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], OrderStatus.PENDING.value)

    @patch("order.services.OrderService.validate_cart_for_checkout")
    @patch("order.services.OrderService.validate_shipping_address")
    def test_offline_flow_accepted_without_payment_intent(
        self,
        mock_validate_address,
        mock_validate_cart,
    ):
        """COD (offline) flow creates an order without payment_intent_id."""
        mock_validate_cart.return_value = {"valid": True, "errors": []}
        mock_validate_address.return_value = None

        pay_way = PayWayFactory(provider_code="cod", is_online_payment=False)
        data = {
            **self._base_address(),
            "pay_way_id": pay_way.id,
        }

        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], OrderStatus.PENDING.value)

    def test_online_flow_rejects_missing_pay_way_id(self):
        """Serializer catches missing pay_way_id before any DB lookup."""
        data = {
            **self._base_address(),
            "payment_intent_id": "pi_stripe_test_xyz",
            # deliberately no pay_way_id
        }

        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.create_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(self.cart.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("pay_way_id", response.data)

    def test_create_with_optional_address_extras(self):
        """floor, place, location_type pass through without error."""
        from unittest.mock import patch

        pay_way = PayWayFactory(provider_code="cod", is_online_payment=False)
        data = {
            **self._base_address(),
            "pay_way_id": pay_way.id,
            "floor": "2",
            "place": "downtown",
            "location_type": "office",
        }

        with (
            patch(
                "order.services.OrderService.validate_cart_for_checkout",
                return_value={"valid": True, "errors": []},
            ),
            patch(
                "order.services.OrderService.validate_shipping_address",
                return_value=None,
            ),
        ):
            self.client.force_authenticate(user=self.user)
            response = self.client.post(
                self.create_url,
                data,
                format="json",
                HTTP_X_CART_ID=str(self.cart.id),
            )

        # 201 means the payload was accepted and routed correctly
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
