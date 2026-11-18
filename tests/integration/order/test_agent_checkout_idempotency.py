"""One agent checkout, one order (#637).

The agent gateway names its checkout (``agentCheckout``) when it creates
the order, and asks again with the same id when an answer never reached
it. Before, a second call created a second order (or was refused for what
the first one did), and the gateway could only report the outcome as
unknown.

What is pinned here:

* the order stores the checkout, and a repeat is answered with that order,
  201 with the same body, on either creation path: no second order, the
  stock taken once, even after the first order kept, emptied or deleted
  the cart;
* only the gateway can name a checkout: without its secret the field is
  ignored, so no other caller ties an order to a checkout or is answered
  an order by naming one;
* ``Order.agent_checkout`` is unique in the database, NULL repeatable,
  and a second order that raced the lookup on another cart is rejected by
  the index and answered with the first;
* two calls of one checkout racing on real connections end with one
  order, and both are answered with it.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APIClient

from cart.models.cart import Cart
from cart.models.item import CartItem
from country.factories import CountryFactory
from order.checkout.attribution import AttributionInput
from order.enum.status import PaymentStatus
from order.exceptions import AgentCheckoutOrderedError
from order.factories.order import OrderFactory
from order.models.order import Order
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from region.factories import RegionFactory
from shipping.models import ShippingProvider
from tests.utils.legal import checkout_acceptance
from tests.utils.shipping import enable_rate

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

AGENT = AttributionInput(agent_protocol="ucp")
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

    def __init__(self, product=None):
        self.country = CountryFactory()
        enable_rate(self.country)
        self.product = product or ProductFactory.create(
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

    def order(self, checkout: UUID | None, *, pay_way=None) -> Order:
        """Place an agent order the order-first way (Viva unless told)."""
        return OrderService.create_order_from_cart_offline(
            legal_acceptance=checkout_acceptance(),
            cart=self.cart,
            shipping_address=self.address(),
            pay_way=pay_way or _viva(),
            attribution=AGENT,
            agent_checkout=checkout,
        )

    def pay_first(self, checkout: UUID) -> Order:
        """Place an agent order the payment-first way, on a Stripe intent."""
        provider = Mock()
        provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {},
        )
        with patch(
            "order.payments.payment.get_payment_provider", return_value=provider
        ):
            return OrderService.create_order_from_cart(
                legal_acceptance=checkout_acceptance(),
                cart=self.cart,
                shipping_address=self.address(),
                payment_intent_id="pi_agent_checkout",
                pay_way=PayWayFactory.create_online_payment("stripe"),
                attribution=AGENT,
                agent_checkout=checkout,
            )

    def stock(self) -> int:
        self.product.refresh_from_db()
        return self.product.stock


class TestTheServiceAnswersARepeatWithTheOrder:
    def test_the_order_stores_its_checkout(self):
        checkout = uuid4()

        order = _Cart().order(checkout)

        assert Order.objects.get(pk=order.pk).agent_checkout == checkout

    def test_a_repeat_is_the_same_order_and_takes_the_stock_once(self):
        """Viva keeps the cart, so without the lookup the repeat would be
        refused as an order on a cart whose order awaits its payment."""
        cart = _Cart()
        checkout = uuid4()
        first = cart.order(checkout)

        second = cart.order(checkout)

        assert second.pk == first.pk
        assert list(Order.objects.values_list("pk", flat=True)) == [first.pk]
        assert cart.stock() == STOCK - 1

    @pytest.mark.usefixtures("trust_intent_binding")
    def test_the_payment_first_path_stores_and_answers_it_too(self):
        cart = _Cart()
        checkout = uuid4()
        first = cart.pay_first(checkout)

        second = cart.pay_first(checkout)

        assert first.agent_checkout == checkout
        assert second.pk == first.pk
        assert Order.objects.count() == 1

    def test_a_repeat_does_not_send_a_second_confirmation(self):
        cart = _Cart()
        checkout = uuid4()
        cart.order(checkout, pay_way=_cod())
        CartItem.objects.create(
            cart=cart.cart, product=cart.product, quantity=1
        )

        with patch(
            "order.signals.handlers.send_admin_new_order_email.apply_async"
        ) as admin_email:
            cart.order(checkout, pay_way=_cod())

        admin_email.assert_not_called()

    def test_another_checkout_is_another_order(self):
        cart = _Cart()
        first = cart.order(uuid4(), pay_way=_cod())
        CartItem.objects.create(
            cart=cart.cart, product=cart.product, quantity=1
        )

        second = cart.order(uuid4(), pay_way=_cod())

        assert second.pk != first.pk


class TestTheCheckoutIsUniqueInTheDatabase:
    def test_a_second_order_on_one_checkout_is_refused(self):
        checkout = uuid4()
        OrderFactory(agent_checkout=checkout)

        with pytest.raises(IntegrityError), transaction.atomic():
            OrderFactory(agent_checkout=checkout)

    def test_orders_with_no_checkout_coexist(self):
        OrderFactory(agent_checkout=None)
        OrderFactory(agent_checkout=None)

        assert Order.objects.filter(agent_checkout__isnull=True).count() >= 2

    def test_a_second_cart_that_raced_the_lookup_is_rejected_by_the_index(
        self,
    ):
        """Calls of one checkout on one cart serialise on its lock; on two
        carts both can miss the lookup, and the index then rejects the
        second insert. It comes out typed, so the view answers it with
        the first order, and the refused call keeps nothing it took. The
        race is simulated by blinding the lookup; the index is real."""
        product = ProductFactory.create(
            stock=STOCK,
            price=Money(Decimal("10.00"), "EUR"),
            discount_percent=Decimal(0),
            active=True,
            num_images=0,
            num_reviews=0,
        )
        checkout = uuid4()
        first_cart, second_cart = _Cart(product), _Cart(product)

        with patch.object(
            OrderService, "order_for_agent_checkout", return_value=None
        ):
            first = first_cart.order(checkout, pay_way=_cod())
            with pytest.raises(AgentCheckoutOrderedError) as caught:
                second_cart.order(checkout, pay_way=_cod())

        assert isinstance(caught.value.__cause__, IntegrityError)
        assert list(Order.objects.values_list("pk", flat=True)) == [first.pk]
        product.refresh_from_db()
        assert product.stock == STOCK - 1


@pytest.mark.usefixtures("checkout_legal_documents")
class TestThroughTheEndpoint:
    """``POST /order`` as the gateway sends it: a guest cart in
    ``X-Cart-Id``, the shared secret in ``X-Internal-Gateway``, its
    protocol in ``attribution`` and its checkout in ``agentCheckout``."""

    @pytest.fixture(autouse=True)
    def _a_store_with_viva_and_cod(self):
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
            self.cod = _cod()
            yield

    def _post(
        self, pay_way, checkout, *, secret=GATEWAY_SECRET, cart=None, **body
    ):
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
                "attribution": {"agentProtocol": "ucp"},
                "agentCheckout": str(checkout),
                **body,
            },
            format="json",
            HTTP_X_CART_ID=str((cart or self.checkout.cart).uuid),
            HTTP_X_INTERNAL_GATEWAY=secret,
            HTTP_X_LANGUAGE="en",
        )

    @pytest.mark.parametrize("pay_way", ["viva", "cod"])
    def test_a_repeat_is_answered_with_the_same_order(
        self, pay_way, documented
    ):
        checkout = uuid4()
        first = self._post(getattr(self, pay_way), checkout)
        assert first.status_code == status.HTTP_201_CREATED, first.json()

        second = self._post(getattr(self, pay_way), checkout)

        assert second.status_code == status.HTTP_201_CREATED, second.json()
        # The same order, read again: only the timeline's timestamps differ
        # in spelling (the first answer holds the in-memory UTC value).
        timeline = "orderTimeline"
        assert {k: v for k, v in second.json().items() if k != timeline} == {
            k: v for k, v in first.json().items() if k != timeline
        }
        assert first.json()["agentCheckout"] == str(checkout)
        documented(second, "/api/v1/order", "post")
        assert Order.objects.count() == 1
        assert self.checkout.stock() == STOCK - 1

    def test_a_repeat_after_the_cart_is_gone(self):
        """A cash-on-delivery order deletes its cart once it commits, so
        the repeat names a cart that no longer exists."""
        checkout = uuid4()
        first = self._post(self.cod, checkout).json()
        Cart.objects.filter(pk=self.checkout.cart.pk).delete()

        second = self._post(self.cod, checkout)

        assert second.status_code == status.HTTP_201_CREATED, second.json()
        assert second.json()["id"] == first["id"]

    def test_a_repeat_is_answered_before_the_body_is_validated(self):
        """Validation reads rows that can change after the first order:
        here its carrier is switched off, which refuses a new order with
        that carrier, and must not refuse the repeat of one that exists
        (the gateway reads a 4xx as "nothing placed")."""
        checkout = uuid4()
        carrier = {"shippingProviderCode": "flat_rate"}
        first = self._post(self.cod, checkout, **carrier)
        assert first.status_code == status.HTTP_201_CREATED, first.json()
        # Made before the switch: a new cart's rate re-enables its carrier.
        other = _Cart()
        ShippingProvider.objects.filter(code="flat_rate").update(
            is_active=False
        )
        refused = self._post(self.cod, uuid4(), cart=other.cart, **carrier)
        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert refused.json()["code"] == "shipping_provider_unavailable"

        second = self._post(self.cod, checkout, **carrier)

        assert second.status_code == status.HTTP_201_CREATED, second.json()
        assert second.json()["id"] == first.json()["id"]
        assert Order.objects.count() == 1

    def test_a_repeat_whose_order_was_deleted_is_answered_with_it(self):
        """The checkout placed it: deleting it does not let the checkout
        place another."""
        checkout = uuid4()
        first = self._post(self.cod, checkout).json()
        Order.objects.filter(pk=first["id"]).update(
            is_deleted=True, deleted_at=timezone.now()
        )

        second = self._post(self.cod, checkout)

        assert second.status_code == status.HTTP_201_CREATED, second.json()
        assert second.json()["id"] == first["id"]
        assert Order.objects.all_with_deleted().count() == 1

    def test_a_call_the_index_refused_is_answered_with_the_first_order(self):
        """Two carts of one checkout: the second call misses both lookups
        (blinded here), its insert is rejected by the real index, and the
        view's lookup after the refusal answers the first order."""
        checkout = uuid4()
        first = self._post(self.cod, checkout).json()
        other = _Cart()
        real_lookup = OrderService.order_for_agent_checkout
        lookups: list[UUID] = []

        def blind_twice(agent_checkout):
            lookups.append(agent_checkout)
            return None if len(lookups) <= 2 else real_lookup(agent_checkout)

        with patch.object(
            OrderService, "order_for_agent_checkout", side_effect=blind_twice
        ):
            second = self._post(self.cod, checkout, cart=other.cart)

        assert len(lookups) == 3, "the lookup after the refusal never ran"
        assert second.status_code == status.HTTP_201_CREATED, second.json()
        assert second.json()["id"] == first["id"]
        assert Order.objects.count() == 1
        assert other.stock() == STOCK, "the refused call kept its stock"

    @pytest.mark.parametrize("secret", ["", "guess"])
    def test_without_the_secret_the_checkout_is_ignored(self, secret):
        checkout = uuid4()
        placed = self._post(self.viva, checkout).json()
        other = _Cart()

        response = self._post(
            self.viva, checkout, secret=secret, cart=other.cart
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert response.json()["id"] != placed["id"]
        assert response.json()["agentCheckout"] is None
        assert Order.objects.count() == 2

    def test_an_order_without_a_checkout_shows_none(self):
        response = APIClient().post(
            reverse("order-list"),
            {
                "payWayId": self.cod.id,
                "termsAccepted": True,
                "firstName": "Maria",
                "lastName": "Papadopoulou",
                "email": "maria@example.com",
                "street": "Ermou",
                "streetNumber": "1",
                "city": "Athens",
                "zipcode": "10563",
                "countryId": self.checkout.country.alpha_2,
                "regionId": self.region.alpha,
                "phone": "+306900000000",
            },
            format="json",
            HTTP_X_CART_ID=str(self.checkout.cart.uuid),
            HTTP_X_LANGUAGE="en",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert response.json()["agentCheckout"] is None

    def test_a_malformed_checkout_is_refused(self):
        response = self._post(self.cod, "not-a-uuid")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "agentCheckout" in response.json()["fields"]
        assert Order.objects.count() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("checkout_legal_documents")
class TestTwoCallsOfOneCheckoutRacing:
    """Real commits on real connections: the cart row lock is the point.
    The second call waits for it, then finds the first one's order."""

    @staticmethod
    def _race(call) -> tuple[list, list[BaseException]]:
        barrier = threading.Barrier(2)
        results: list = []
        failures: list[BaseException] = []

        def run():
            try:
                barrier.wait(timeout=30)
                results.append(call())
            except BaseException as exc:
                failures.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        return results, failures

    def test_in_the_service_end_with_one_order(self):
        cart = _Cart()
        pay_way = _viva()
        acceptance = checkout_acceptance()
        checkout = uuid4()

        placed, failures = self._race(
            lambda: (
                OrderService.create_order_from_cart_offline(
                    legal_acceptance=acceptance,
                    cart=cart.cart,
                    shipping_address=cart.address(),
                    pay_way=pay_way,
                    attribution=AGENT,
                    agent_checkout=checkout,
                ).pk
            )
        )

        assert not failures, failures
        assert len(placed) == 2
        assert list(Order.objects.values_list("pk", flat=True)) == [placed[0]]
        assert placed[0] == placed[1]
        assert cart.stock() == STOCK - 1, "the stock was taken twice"

    def test_through_the_endpoint_both_are_answered_the_one_order(self):
        cart = _Cart()
        region = RegionFactory(country=cart.country)
        cod = _cod()
        checkout = uuid4()
        address = cart.address()
        body = {
            "payWayId": cod.id,
            "termsAccepted": True,
            "firstName": address["first_name"],
            "lastName": address["last_name"],
            "email": address["email"],
            "street": address["street"],
            "streetNumber": address["street_number"],
            "city": address["city"],
            "zipcode": address["zipcode"],
            "countryId": address["country_id"],
            "regionId": region.alpha,
            "phone": address["phone"],
            "attribution": {"agentProtocol": "ucp"},
            "agentCheckout": str(checkout),
        }

        def post():
            response = APIClient().post(
                reverse("order-list"),
                body,
                format="json",
                HTTP_X_CART_ID=str(cart.cart.uuid),
                HTTP_X_INTERNAL_GATEWAY=GATEWAY_SECRET,
                HTTP_X_LANGUAGE="en",
            )
            return response.status_code, response.json().get("id")

        with (
            override_settings(AGENT_GATEWAY_INTERNAL_SECRET=GATEWAY_SECRET),
            patch(
                "pay_way.services.PayWayService.is_provider_configured",
                return_value=True,
            ),
        ):
            answers, failures = self._race(post)

        assert not failures, failures
        assert [code for code, _ in answers] == [201, 201], answers
        assert answers[0][1] == answers[1][1]
        assert list(Order.objects.values_list("pk", flat=True)) == [
            answers[0][1]
        ]
        assert cart.stock() == STOCK - 1, "the stock was taken twice"
