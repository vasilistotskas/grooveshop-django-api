"""A cart's own stock holds never block the cart.

``POST cart/reserve-stock`` used to add a fresh hold on top of the ones
the same cart already had, and ``StockManager.reserve_stock`` counted every
active hold, the caller's own included. With ``stock == quantity`` the
second attempt (a reload, a retry, an agent completing twice) answered 409
and the first attempt's units stayed held for the TTL. The call now
replaces the cart's holds, whatever products they were on.
"""

from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.enum.status import PaymentStatus
from order.exceptions import CartStockUnavailableError
from order.models import StockLog, StockReservation
from order.services import OrderService
from order.stock import StockManager
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from tests.utils.legal import checkout_acceptance
from tests.utils.shipping import enable_rate
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


def _client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _reserve(client: APIClient):
    return client.post(reverse("cart-reserve-stock"))


def _active(cart, **filters):
    return StockReservation.objects.filter(
        session_id=str(cart.uuid), consumed=False, **filters
    )


@pytest.fixture
def user():
    return UserAccountFactory(num_addresses=0)


@pytest.fixture
def cart(user):
    return CartFactory(user=user, num_cart_items=0)


class TestSameCartReservesAgain:
    def test_second_reserve_succeeds_when_stock_equals_quantity(
        self, user, cart
    ):
        product = ProductFactory(stock=3)
        CartItemFactory(cart=cart, product=product, quantity=3)
        client = _client_for(user)

        first = _reserve(client)
        second = _reserve(client)

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK, second.data
        held = _active(cart)
        assert [r.quantity for r in held] == [3]
        assert [r.id for r in held] == second.data["reservation_ids"]

    def test_replaced_holds_are_released_and_audited(self, user, cart):
        product = ProductFactory(stock=3)
        CartItemFactory(cart=cart, product=product, quantity=3)
        client = _client_for(user)

        first_ids = _reserve(client).data["reservation_ids"]
        _reserve(client)

        old = StockReservation.objects.get(id=first_ids[0])
        assert old.consumed is True
        assert old.order_id is None
        assert (
            StockLog.objects.filter(
                product=product, operation_type=StockLog.OPERATION_RELEASE
            ).count()
            == 1
        )

    def test_identical_retry_leaves_one_hold_per_line(self, user, cart):
        first_product = ProductFactory(stock=10)
        second_product = ProductFactory(stock=10)
        CartItemFactory(cart=cart, product=first_product, quantity=2)
        CartItemFactory(cart=cart, product=second_product, quantity=4)
        client = _client_for(user)

        for _ in range(3):
            response = _reserve(client)
            assert response.status_code == status.HTTP_200_OK

        assert _active(cart).count() == 2
        assert StockManager.get_available_stock(first_product.id) == 8
        assert StockManager.get_available_stock(second_product.id) == 6

    def test_quantity_up_down_and_line_removed_between_attempts(
        self, user, cart
    ):
        kept = ProductFactory(stock=5)
        dropped = ProductFactory(stock=5)
        kept_line = CartItemFactory(cart=cart, product=kept, quantity=2)
        dropped_line = CartItemFactory(cart=cart, product=dropped, quantity=3)
        client = _client_for(user)
        assert _reserve(client).status_code == status.HTTP_200_OK

        kept_line.quantity = 5
        kept_line.save()
        assert _reserve(client).status_code == status.HTTP_200_OK
        assert _active(cart, product=kept).get().quantity == 5

        kept_line.quantity = 1
        kept_line.save()
        dropped_line.delete()
        assert _reserve(client).status_code == status.HTTP_200_OK
        assert _active(cart, product=kept).get().quantity == 1
        assert not _active(cart, product=dropped).exists()
        assert StockManager.get_available_stock(dropped.id) == 5

    def test_expired_hold_of_the_cart_is_replaced(self, user, cart):
        product = ProductFactory(stock=2)
        CartItemFactory(cart=cart, product=product, quantity=2)
        expired = StockReservation.objects.create(
            product=product,
            quantity=2,
            reserved_by=user,
            session_id=str(cart.uuid),
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        response = _reserve(_client_for(user))

        assert response.status_code == status.HTTP_200_OK
        expired.refresh_from_db()
        assert expired.consumed is True
        assert _active(cart).get().quantity == 2

    def test_failed_reserve_keeps_the_previous_holds(self, user, cart):
        product = ProductFactory(stock=4)
        line = CartItemFactory(cart=cart, product=product, quantity=2)
        client = _client_for(user)
        previous = _reserve(client).data["reservation_ids"]

        line.quantity = 9
        line.save()
        response = _reserve(client)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["code"] == "insufficient_stock"
        assert [r.id for r in _active(cart)] == previous


class TestCompetingCarts:
    def test_other_cart_cannot_take_the_last_unit(self, user, cart):
        product = ProductFactory(stock=1)
        CartItemFactory(cart=cart, product=product, quantity=1)
        other_user = UserAccountFactory(num_addresses=0)
        other_cart = CartFactory(user=other_user, num_cart_items=0)
        CartItemFactory(cart=other_cart, product=product, quantity=1)

        assert _reserve(_client_for(user)).status_code == status.HTTP_200_OK
        blocked = _reserve(_client_for(other_user))

        assert blocked.status_code == status.HTTP_409_CONFLICT
        assert blocked.data["failed_items"][0]["available"] == 0
        assert not _active(other_cart).exists()

    def test_holder_can_reserve_again_while_the_other_cart_waits(
        self, user, cart
    ):
        product = ProductFactory(stock=1)
        CartItemFactory(cart=cart, product=product, quantity=1)
        other_user = UserAccountFactory(num_addresses=0)
        other_cart = CartFactory(user=other_user, num_cart_items=0)
        CartItemFactory(cart=other_cart, product=product, quantity=1)

        assert _reserve(_client_for(user)).status_code == status.HTTP_200_OK
        assert (
            _reserve(_client_for(other_user)).status_code
            == status.HTTP_409_CONFLICT
        )
        assert _reserve(_client_for(user)).status_code == status.HTTP_200_OK
        assert _active(cart).count() == 1

    def test_other_carts_holds_are_never_touched(self, user, cart):
        product = ProductFactory(stock=10)
        CartItemFactory(cart=cart, product=product, quantity=4)
        other_user = UserAccountFactory(num_addresses=0)
        other_cart = CartFactory(user=other_user, num_cart_items=0)
        CartItemFactory(cart=other_cart, product=product, quantity=5)
        _reserve(_client_for(other_user))

        _reserve(_client_for(user))
        _reserve(_client_for(user))

        assert _active(other_cart).get().quantity == 5
        assert StockManager.get_available_stock(product.id) == 1


@pytest.mark.usefixtures("trust_intent_binding")
class TestOrderCreationAfterReReserve:
    @pytest.fixture
    def country(self):
        country = CountryFactory(alpha_2="US", name="United States")
        enable_rate(country)
        return country

    def test_order_consumes_the_current_holds_only(self, user, cart, country):
        product = ProductFactory(stock=3, active=True)
        CartItemFactory(cart=cart, product=product, quantity=3)
        client = _client_for(user)
        first_ids = _reserve(client).data["reservation_ids"]
        current_ids = _reserve(client).data["reservation_ids"]

        with patch("order.payment.get_payment_provider") as provider:
            provider.return_value = Mock(
                get_payment_status=Mock(
                    return_value=(PaymentStatus.COMPLETED, {"status": "ok"})
                )
            )
            order = OrderService.create_order_from_cart(
                legal_acceptance=checkout_acceptance(),
                cart=cart,
                shipping_address={
                    "first_name": "Test",
                    "last_name": "User",
                    "email": "test@example.com",
                    "street": "123 Test St",
                    "street_number": "1",
                    "city": "Test City",
                    "zipcode": "12345",
                    "country_id": country.alpha_2,
                    "phone": "+1234567890",
                    "shipping_kind": "home_delivery",
                },
                payment_intent_id="pi_replace_holds",
                pay_way=PayWayFactory(provider_code="stripe"),
                user=user,
            )

        assert order.metadata["stock_reservation_ids"] == current_ids
        linked = StockReservation.objects.filter(order=order)
        assert [r.id for r in linked] == current_ids
        assert StockReservation.objects.get(id=first_ids[0]).order_id is None
        assert not _active(cart).exists()
        product.refresh_from_db()
        assert product.stock == 0
        assert (
            StockLog.objects.filter(
                product=product,
                order=order,
                operation_type=StockLog.OPERATION_DECREMENT,
            ).count()
            == 1
        )


class TestReserveCartStock:
    def test_replaces_session_holds_across_products(self, user):
        kept = ProductFactory(stock=5)
        dropped = ProductFactory(stock=5)
        session = "cart-session"
        StockManager.reserve_cart_stock(
            {kept.id: 2, dropped.id: 3}, session_id=session, user_id=user.id
        )

        holds = StockManager.reserve_cart_stock(
            {kept.id: 5}, session_id=session, user_id=user.id
        )

        assert [(h.product_id, h.quantity) for h in holds] == [(kept.id, 5)]
        live = StockReservation.objects.filter(
            session_id=session, consumed=False
        )
        assert [r.product_id for r in live] == [kept.id]
        assert StockManager.get_available_stock(dropped.id) == 5

    def test_reports_every_short_line_and_changes_nothing(self, user):
        first = ProductFactory(stock=1)
        second = ProductFactory(stock=1)
        ok = ProductFactory(stock=9)
        session = "cart-session"
        StockManager.reserve_cart_stock(
            {ok.id: 1}, session_id=session, user_id=user.id
        )

        with pytest.raises(CartStockUnavailableError) as raised:
            StockManager.reserve_cart_stock(
                {first.id: 2, second.id: 2, ok.id: 3},
                session_id=session,
                user_id=user.id,
            )

        assert {e.product_id for e in raised.value.shortfalls} == {
            first.id,
            second.id,
        }
        live = StockReservation.objects.filter(
            session_id=session, consumed=False
        )
        assert [(r.product_id, r.quantity) for r in live] == [(ok.id, 1)]

    def test_rejects_a_non_positive_quantity(self, user):
        product = ProductFactory(stock=5)
        with pytest.raises(ValueError, match="positive"):
            StockManager.reserve_cart_stock(
                {product.id: 0}, session_id="s", user_id=user.id
            )

    def test_session_lock_is_taken_before_any_product_lock(self, user):
        product = ProductFactory(stock=5)

        with CaptureQueriesContext(connection) as queries:
            StockManager.reserve_cart_stock(
                {product.id: 1}, session_id="cart-session", user_id=user.id
            )

        statements = [q["sql"] for q in queries]
        lock_at = next(
            i
            for i, sql in enumerate(statements)
            if "pg_advisory_xact_lock" in sql
        )
        product_lock_at = next(
            i
            for i, sql in enumerate(statements)
            if 'FROM "product_product"' in sql and "FOR UPDATE" in sql
        )
        assert lock_at < product_lock_at
