"""An agent order is refused while its cart's earlier order awaits payment.

An order paid online keeps its cart until the payment lands
(``Order.awaits_online_payment``), so the same lines can be ordered again.
On the storefront a person does that on purpose ("Back, then order again")
and it keeps working. Through the agent gateway it is a platform that lost
its checkout or sent it twice: a second order for the same goods, with its
own stock decrement, that the buyer never asked for (#692).

What is pinned here:

* an order the gateway places (a believed ``agentProtocol``) on such a cart
  is refused with ``cart_has_unpaid_order``, whatever its own pay way, and
  changes nothing;
* an agent order on a verified payment intent is placed all the same: its
  payment is already taken, and a refusal would keep money for no order;
* the same call without a believed agent protocol places the second order;
* the earlier order counts only while it is this cart's and still awaits
  its online payment;
* the refusal names no order;
* two agent orders racing for one cart end with one order.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APIClient

from cart.models.cart import Cart
from cart.models.item import CartItem
from country.factories import CountryFactory
from order.attribution import AttributionInput
from order.enum.attribution import OrderSourceType
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import CartHasUnpaidOrderError, InvalidOrderDataError
from order.models.order import Order
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from region.factories import RegionFactory
from tests.utils.legal import checkout_acceptance
from tests.utils.shipping import enable_rate

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

AGENT = AttributionInput(agent_protocol="ucp")
STOREFRONT = AttributionInput(utm_source="google", click_ids=("gclid",))
GATEWAY_SECRET = "gateway-secret"
STOCK = 10


def _viva():
    return PayWayFactory.create_online_payment("viva_wallet")


def _cod():
    return PayWayFactory(
        provider_code="cod",
        settlement=PaySettlement.COURIER_CASH,
        active=True,
        cost=Money(Decimal(0), "EUR"),
        free_threshold=Money(Decimal(0), "EUR"),
    )


class _Cart:
    """A guest cart with one line, as the gateway builds one for a
    checkout, in a country the store ships to."""

    def __init__(self):
        self.country = CountryFactory()
        enable_rate(self.country)
        self.product = ProductFactory.create(
            stock=STOCK,
            price=Money(Decimal("10.00"), "EUR"),
            discount_percent=Decimal(0),
            active=True,
            num_images=0,
            num_reviews=0,
        )
        self.cart = Cart.objects.create(user=None)
        CartItem.objects.create(
            cart=self.cart, product=self.product, quantity=1
        )

    def address(self) -> dict:
        return {
            "first_name": "Maria",
            "last_name": "Papadopoulou",
            "email": "maria@example.com",
            "street": "Ermou",
            "street_number": "1",
            "city": "Athens",
            "zipcode": "10563",
            "country_id": self.country.alpha_2,
            "phone": "+306900000000",
            "shipping_kind": "home_delivery",
        }

    def order(self, *, pay_way=None, attribution=None) -> Order:
        """Place an order the order-first way (Viva unless told)."""
        return OrderService.create_order_from_cart_offline(
            legal_acceptance=checkout_acceptance(),
            cart=self.cart,
            shipping_address=self.address(),
            pay_way=pay_way or _viva(),
            attribution=attribution,
        )

    def pay_first(self, *, attribution=None) -> Order:
        """Place an order the payment-first way, on a Stripe intent."""
        provider = Mock()
        provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {},
        )
        with patch("order.payment.get_payment_provider", return_value=provider):
            return OrderService.create_order_from_cart(
                legal_acceptance=checkout_acceptance(),
                cart=self.cart,
                shipping_address=self.address(),
                payment_intent_id="pi_agent_second_order",
                pay_way=PayWayFactory.create_online_payment("stripe"),
                attribution=attribution,
            )

    def stock(self) -> int:
        self.product.refresh_from_db()
        return self.product.stock

    def lines(self) -> list[tuple[int, int]]:
        return list(self.cart.items.values_list("product_id", "quantity"))


def _set(order: Order, **fields) -> None:
    """Put *order* in a state without running what leads to it."""
    Order.objects.all_with_deleted().filter(pk=order.pk).update(**fields)


def _state(order: Order) -> tuple:
    row = Order.objects.get(pk=order.pk)
    return (row.status, row.payment_status, row.updated_at, row.metadata)


class TestAnAgentOrderIsRefused:
    def test_while_the_carts_earlier_order_awaits_its_online_payment(self):
        checkout = _Cart()
        earlier = checkout.order()
        before = (_state(earlier), checkout.stock(), checkout.lines())
        assert before[1:] == (STOCK - 1, [(checkout.product.pk, 1)])

        with pytest.raises(CartHasUnpaidOrderError) as excinfo:
            checkout.order(attribution=AGENT)

        assert "already waiting for its payment" in str(excinfo.value)
        assert list(Order.objects.values_list("pk", flat=True)) == [earlier.pk]
        assert (_state(earlier), checkout.stock(), checkout.lines()) == before

    def test_the_earlier_order_may_be_an_agents_own(self):
        checkout = _Cart()
        checkout.order(attribution=AGENT)

        with pytest.raises(CartHasUnpaidOrderError):
            checkout.order(attribution=AGENT)

        assert Order.objects.count() == 1

    def test_a_cash_on_delivery_order_is_refused_too(self):
        checkout = _Cart()
        checkout.order()

        with pytest.raises(CartHasUnpaidOrderError):
            checkout.order(pay_way=_cod(), attribution=AGENT)

        assert Order.objects.count() == 1
        assert checkout.stock() == STOCK - 1

    @pytest.mark.parametrize(
        "payment_status", [PaymentStatus.FAILED, PaymentStatus.PROCESSING]
    )
    def test_a_payment_that_failed_or_is_under_way_is_still_awaited(
        self, payment_status
    ):
        """A failed attempt does not end the payment: the same Viva
        payment order stays payable until its own expiry."""
        checkout = _Cart()
        _set(checkout.order(), payment_status=payment_status)

        with pytest.raises(CartHasUnpaidOrderError):
            checkout.order(attribution=AGENT)

    def test_callers_that_catch_invalid_order_data_still_do(self):
        assert issubclass(CartHasUnpaidOrderError, InvalidOrderDataError)


class TestTheStorefrontIsUnchanged:
    @pytest.mark.parametrize("attribution", [None, STOREFRONT])
    def test_a_second_order_from_the_cart_is_placed(self, attribution):
        checkout = _Cart()
        earlier = checkout.order()

        second = checkout.order(attribution=attribution)

        assert second.pk != earlier.pk
        assert Order.objects.count() == 2
        assert checkout.stock() == STOCK - 2

    def test_an_agent_protocol_django_does_not_know_is_not_an_agents(self):
        checkout = _Cart()
        checkout.order()

        checkout.order(attribution=AttributionInput(agent_protocol="a2a"))

        assert Order.objects.count() == 2


class TestAnAgentOrderGoesThrough:
    def test_on_a_cart_with_no_order(self):
        checkout = _Cart()

        order = checkout.order(attribution=AGENT)

        assert order.attribution.source_type == OrderSourceType.AGENT

    @pytest.mark.parametrize(
        "ended",
        [
            pytest.param(
                {"payment_status": PaymentStatus.COMPLETED}, id="paid"
            ),
            pytest.param(
                {
                    "status": OrderStatus.CANCELED,
                    "payment_status": PaymentStatus.CANCELED,
                },
                id="canceled",
            ),
            pytest.param(
                {"payment_status": PaymentStatus.REFUNDED}, id="refunded"
            ),
            pytest.param(
                {"is_deleted": True, "deleted_at": timezone.now()},
                id="soft-deleted",
            ),
        ],
    )
    def test_once_the_earlier_order_no_longer_awaits_a_payment(self, ended):
        checkout = _Cart()
        _set(checkout.order(), **ended)

        order = checkout.order(attribution=AGENT)

        assert order.attribution.source_type == OrderSourceType.AGENT
        assert checkout.stock() == STOCK - 2

    def test_when_the_earlier_order_is_settled_offline(self):
        checkout = _Cart()
        _set(checkout.order(), pay_way=_cod())

        checkout.order(attribution=AGENT)

        assert Order.objects.count() == 2

    @pytest.mark.usefixtures("trust_intent_binding")
    def test_on_a_verified_intent_whose_payment_is_already_taken(self):
        """The payment-first path is not checked: the intent is paid when
        the order is created, so a refusal would keep money for no order,
        and the order placed is a paid one."""
        checkout = _Cart()
        earlier = checkout.order()

        order = checkout.pay_first(attribution=AGENT)

        assert order.pk != earlier.pk
        assert order.attribution.source_type == OrderSourceType.AGENT
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.payment_id == "pi_agent_second_order"
        assert Order.objects.count() == 2
        assert checkout.stock() == STOCK - 2

    def test_when_the_unpaid_order_is_another_carts(self):
        other = _Cart()
        other.order()
        checkout = _Cart()

        checkout.order(attribution=AGENT)

        assert Order.objects.count() == 2


@pytest.mark.usefixtures("checkout_legal_documents")
class TestThroughTheEndpoint:
    """``POST /order`` as the gateway sends it: a guest cart in
    ``X-Cart-Id``, the shared secret in ``X-Internal-Gateway`` and its
    protocol in ``attribution``."""

    @pytest.fixture(autouse=True)
    def _a_store_with_viva(self):
        with (
            override_settings(AGENT_GATEWAY_INTERNAL_SECRET=GATEWAY_SECRET),
            patch(
                "pay_way.services.PayWayService.is_provider_configured",
                return_value=True,
            ),
        ):
            self.checkout = _Cart()
            self.region = RegionFactory(country=self.checkout.country)
            self.viva = _viva()
            yield

    def _post(self, pay_way, *, secret=GATEWAY_SECRET, **body):
        address = self.checkout.address()
        return APIClient().post(
            reverse("order-list"),
            {
                "payWayId": pay_way.id,
                "termsAccepted": True,
                "firstName": address["first_name"],
                "lastName": address["last_name"],
                "email": address["email"],
                "street": address["street"],
                "streetNumber": address["street_number"],
                "city": address["city"],
                "zipcode": address["zipcode"],
                "countryId": address["country_id"],
                "regionId": self.region.alpha,
                "phone": address["phone"],
                **body,
            },
            format="json",
            HTTP_X_CART_ID=str(self.checkout.cart.uuid),
            HTTP_X_INTERNAL_GATEWAY=secret,
            HTTP_X_LANGUAGE="en",
        )

    def _as_gateway(self, pay_way, **kwargs):
        return self._post(
            pay_way, attribution={"agentProtocol": "acp"}, **kwargs
        )

    def test_the_gateway_is_refused_with_the_code(self, documented):
        first = self._as_gateway(self.viva)
        assert first.status_code == status.HTTP_201_CREATED, first.json()

        response = self._as_gateway(self.viva)

        message = (
            "An order placed from this cart is already waiting for its "
            "payment. Pay for it or cancel it before placing another."
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "cart": [message],
            "code": "cart_has_unpaid_order",
            "fields": {
                "cart": [{"message": message, "code": "cart_has_unpaid_order"}]
            },
        }
        documented(response, "/api/v1/order", "post")
        assert Order.objects.count() == 1
        assert self.checkout.stock() == STOCK - 1

    def test_the_refusal_names_no_order(self):
        first = self._as_gateway(self.viva).json()

        response = self._as_gateway(_cod())

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.content.decode()
        assert first["uuid"] not in body
        assert set(response.json()) == {"cart", "code", "fields"}
        assert not any(character.isdigit() for character in body), body

    def test_the_storefront_places_the_second_order(self):
        first = self._post(self.viva, secret="")
        second = self._post(self.viva, secret="")

        assert first.status_code == status.HTTP_201_CREATED, first.json()
        assert second.status_code == status.HTTP_201_CREATED, second.json()
        assert second.json()["id"] != first.json()["id"]
        assert self.checkout.stock() == STOCK - 2

    @pytest.mark.parametrize("secret", ["", "guess"])
    def test_an_agent_claim_without_the_secret_is_the_storefronts(self, secret):
        self._as_gateway(self.viva)

        response = self._as_gateway(self.viva, secret=secret)

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert response.json()["attribution"]["sourceType"] != "agent"
        assert Order.objects.count() == 2


@pytest.mark.django_db(transaction=True)
class TestTwoAgentOrdersRacingForOneCart:
    """Real commits on real connections: the cart row lock is the point.
    The second order waits for it, and then finds the first one's."""

    def test_end_with_one_order(self):
        checkout = _Cart()
        pay_way = _viva()
        acceptance = checkout_acceptance()
        barrier = threading.Barrier(2)
        placed: list[int] = []
        refused: list[CartHasUnpaidOrderError] = []
        failures: list[BaseException] = []

        def submit():
            try:
                barrier.wait(timeout=30)
                placed.append(
                    OrderService.create_order_from_cart_offline(
                        legal_acceptance=acceptance,
                        cart=checkout.cart,
                        shipping_address=checkout.address(),
                        pay_way=pay_way,
                        attribution=AGENT,
                    ).pk
                )
            except CartHasUnpaidOrderError as exc:
                refused.append(exc)
            except BaseException as exc:
                failures.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        assert not failures, failures
        assert (len(placed), len(refused)) == (1, 1)
        assert list(Order.objects.values_list("pk", flat=True)) == placed
        assert checkout.stock() == STOCK - 1, "the stock was taken twice"
