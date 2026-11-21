"""The client IP an order's Meta events carry is never the caller's choice.

``POST /order`` takes ``meta.client_ip_address`` in the body, and Meta
receives the address stored on the order. The body value is dropped; the
order stores ``core.client_ip.edge_client_ip(request)`` (the visitor
Cloudflare vouched for through ``X-Origin-Verify``) or no address
(``docs/client-ip.md``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from country.factories import CountryFactory
from order.models.order import Order
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from tests.utils.checkout import quoted_body
from tests.utils.shipping import enable_rate
from user.factories.account import UserAccountFactory

FORGED_IP = "198.51.100.77"
VISITOR_IP = "203.0.113.9"
SECRET = "s3cret-edge-token"


pytestmark = [
    pytest.mark.usefixtures("trust_intent_binding"),
    pytest.mark.usefixtures("checkout_legal_documents"),
]


@pytest.mark.django_db
@patch(
    "order.services.OrderService.validate_shipping_address",
    return_value=None,
)
@patch(
    "order.services.OrderService.validate_cart_for_checkout",
    return_value={"valid": True, "errors": [], "warnings": []},
)
class TestOrderMetaClientIp(APITestCase):
    def setUp(self):
        super().setUp()
        # Outside a tenant there are no Stripe credentials at all; a
        # stand-in key lets the Stripe pay way count as configured.
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

        self.user = UserAccountFactory()
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)
        enable_rate(self.country)
        self.cart = CartFactory(user=self.user)
        CartItemFactory(
            cart=self.cart,
            product=ProductFactory(
                active=True, stock=10, num_images=0, num_reviews=0
            ),
            quantity=1,
        )
        self.url = reverse("order-list")
        self.client.force_authenticate(user=self.user)

    def _post(self, pay_way, *, headers=None, **extra):
        headers = {"HTTP_X_CART_ID": str(self.cart.uuid), **(headers or {})}
        body = {
            "payWayId": pay_way.id,
            "termsAccepted": True,
            "firstName": "Maria",
            "lastName": "Papadopoulou",
            "email": "maria@example.com",
            "street": "Ermou",
            "streetNumber": "1",
            "city": "Athens",
            "zipcode": "10563",
            "countryId": self.country.alpha_2,
            "regionId": self.region.alpha,
            "phone": "+306900000000",
            **extra,
        }
        return self.client.post(
            self.url,
            quoted_body(self.client, body, **headers),
            format="json",
            **headers,
        )

    def _cod(self):
        return PayWayFactory(
            provider_code="cod",
            settlement=PaySettlement.COURIER_CASH,
            active=True,
        )

    def _stored_meta(self, response):
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        return Order.objects.get(id=response.json()["id"]).metadata["meta"]

    def test_a_body_address_is_dropped_without_the_edge_proof(self, *_mocks):
        response = self._post(
            self._cod(),
            meta={
                "fbp": "fb.1.1.2",
                "client_ip_address": FORGED_IP,
                "consent": {"ads": True},
            },
            headers={
                "HTTP_X_FORWARDED_FOR": FORGED_IP,
                "HTTP_X_REAL_IP": FORGED_IP,
                "HTTP_CF_CONNECTING_IP": FORGED_IP,
            },
        )

        meta = self._stored_meta(response)
        assert meta["fbp"] == "fb.1.1.2"
        assert "client_ip_address" not in meta

    @override_settings(ORIGIN_VERIFY_SECRET=SECRET)
    def test_the_edge_verified_address_replaces_the_body_address(self, *_mocks):
        response = self._post(
            self._cod(),
            meta={
                "fbp": "fb.1.1.2",
                "client_ip_address": FORGED_IP,
                "consent": {"ads": True},
            },
            headers={
                "HTTP_X_ORIGIN_VERIFY": SECRET,
                "HTTP_CF_CONNECTING_IP": VISITOR_IP,
            },
        )

        assert self._stored_meta(response)["client_ip_address"] == VISITOR_IP

    @override_settings(ORIGIN_VERIFY_SECRET=SECRET)
    def test_a_checkout_without_meta_context_stores_no_address(self, *_mocks):
        response = self._post(
            self._cod(),
            headers={
                "HTTP_X_ORIGIN_VERIFY": SECRET,
                "HTTP_CF_CONNECTING_IP": VISITOR_IP,
            },
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        order = Order.objects.get(id=response.json()["id"])
        assert "meta" not in order.metadata
