"""A refused order create carries a stable ``error.type``.

The storefront used to branch on message TEXT — ``detail.includes(
'expired')`` for a lapsed reservation, ``'insufficient stock'`` in the
``cart`` messages — which only ever matched in English, and the
reservation branch could not match at all: the create view answered a
lapsed hold as a generic ``invalid_order_data`` whose ``detail`` never
named it. Now that the API answers in the request's language, text is
not a contract; ``error.type`` is (``OrderCreateErrorType``).
"""

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from country.factories import CountryFactory
from order.enum.create_error import OrderCreateErrorType
from order.exceptions import (
    CartNotReadyError,
    InvalidOrderDataError,
    StockReservationError,
)
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestOrderCreateErrorCodes(APITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserAccountFactory()
        self.pay_way = PayWayFactory(
            provider_code="cod",
            settlement=PaySettlement.COURIER_CASH,
            active=True,
        )
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)
        self.url = reverse("order-list")
        self.client.force_authenticate(user=self.user)

    def _post(self, cart, language="en"):
        return self.client.post(
            self.url,
            {
                "pay_way_id": self.pay_way.id,
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
            },
            format="json",
            HTTP_X_CART_ID=str(cart.uuid),
            HTTP_X_LANGUAGE=language,
        )

    def _cart(self, **product):
        cart = CartFactory(user=self.user)
        item_product = ProductFactory(
            num_images=0, num_reviews=0, **{"active": True, **product}
        )
        CartItemFactory(cart=cart, product=item_product, quantity=3)
        return cart

    def test_insufficient_stock_is_a_code_in_every_language(self):
        for language in ("en", "el"):
            with self.subTest(language=language):
                response = self._post(self._cart(stock=1), language)

                assert response.status_code == status.HTTP_400_BAD_REQUEST
                body = response.json()
                assert (
                    body["error"]["type"]
                    == OrderCreateErrorType.INSUFFICIENT_STOCK
                )
                # The per-item messages stay, for display.
                assert body["cart"]

    def test_any_other_cart_problem_is_cart_invalid(self):
        response = self._post(self._cart(stock=10, active=False))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"]["type"] == (
            OrderCreateErrorType.CART_INVALID
        )

    @patch("order.services.OrderService.validate_shipping_address")
    @patch("order.services.OrderService.create_order_from_cart_offline")
    def test_a_lapsed_reservation_is_reservation_unavailable(
        self, mock_create, _mock_address
    ):
        mock_create.side_effect = StockReservationError(
            "Reservation 7 has expired"
        )

        response = self._post(self._cart(stock=10))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"]["type"] == (
            OrderCreateErrorType.RESERVATION_UNAVAILABLE
        )

    def test_a_failed_cart_is_still_invalid_order_data_to_callers(self):
        """Callers that treat a bad cart as invalid order data keep
        working: the typed error is a subclass."""
        error = CartNotReadyError(["x"], insufficient_stock=True)

        assert isinstance(error, InvalidOrderDataError)
        assert "Cart validation failed" in str(error)
