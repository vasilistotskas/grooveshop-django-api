"""A repeated checkout resumes its cart's unpaid order (#694).

A shopper who goes back and orders the same thing again from the same cart
gets the cart's unpaid online order back, with the stock it already holds,
instead of a second order holding it twice. "The same" is everything the
shopper chose (``order.checkout.resume.checkout_fingerprint``): the lines,
the delivery, the pay way and what is redeemed, and every field of the
address. A changed checkout still places a new order.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from djmoney.money import Money

from cart.models.cart import Cart
from cart.models.item import CartItem
from country.factories import CountryFactory
from order.checkout.resume import FINGERPRINT_KEY
from order.enum.status import OrderStatus, PaymentStatus
from order.models.order import Order
from order.payments import payment_hold
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from tests.utils.checkout import priced
from tests.utils.concurrency import race
from tests.utils.legal import checkout_acceptance
from tests.utils.shipping import enable_rate

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

STOCK = 10


class _Checkout:
    """A guest cart with one line and the choices of one checkout."""

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
        self.item = CartItem.objects.create(
            cart=self.cart, product=self.product, quantity=1
        )
        self.viva = PayWayFactory.create_online_payment("viva_wallet")
        self.acceptance = checkout_acceptance()

    def address(self, **changes) -> dict:
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
            **changes,
        }

    def order(self, *, pay_way=None, **address) -> Order:
        return OrderService.create_order_from_cart_offline(
            **priced(
                legal_acceptance=self.acceptance,
                cart=self.cart,
                shipping_address=self.address(**address),
                pay_way=pay_way or self.viva,
            )
        )

    def stock(self) -> int:
        self.product.refresh_from_db()
        return self.product.stock


class TestTheSameCheckoutResumes:
    def test_it_returns_the_carts_order_and_creates_nothing(self):
        checkout = _Checkout()
        earlier = checkout.order()

        again = checkout.order()

        assert again.pk == earlier.pk
        assert Order.objects.count() == 1
        assert checkout.stock() == STOCK - 1, "the stock was held twice"

    def test_the_order_records_what_resumes_it(self):
        checkout = _Checkout()

        order = checkout.order()

        assert len(order.metadata[FINGERPRINT_KEY]) == 64

    def test_a_failed_attempt_still_resumes(self):
        """A failed payment does not end the order: its Viva payment
        order stays payable until its own expiry."""
        checkout = _Checkout()
        earlier = checkout.order()
        Order.objects.filter(pk=earlier.pk).update(
            payment_status=PaymentStatus.FAILED
        )

        assert checkout.order().pk == earlier.pk


class TestAChangedCheckoutPlacesAnotherOrder:
    def test_another_quantity(self):
        checkout = _Checkout()
        earlier = checkout.order()
        CartItem.objects.filter(pk=checkout.item.pk).update(quantity=2)

        assert checkout.order().pk != earlier.pk

    @pytest.mark.parametrize(
        "change",
        [
            {"email": "other@example.com"},
            {"street_number": "2"},
            {"phone": "+306911111111"},
        ],
    )
    def test_another_address(self, change):
        checkout = _Checkout()
        earlier = checkout.order()

        assert checkout.order(**change).pk != earlier.pk

    def test_another_pay_way(self):
        checkout = _Checkout()
        earlier = checkout.order()

        other = PayWayFactory.create_online_payment("viva_wallet")

        assert checkout.order(pay_way=other).pk != earlier.pk


class TestAnOrderThatCannotBePaidIsNotResumed:
    def test_an_agents_order(self):
        """The gateway's order is never handed to another caller of its
        cart; the agent itself is refused (``cart_has_unpaid_order``)."""
        from order.checkout.attribution import AttributionInput

        checkout = _Checkout()
        agents = OrderService.create_order_from_cart_offline(
            **priced(
                legal_acceptance=checkout.acceptance,
                cart=checkout.cart,
                shipping_address=checkout.address(),
                pay_way=checkout.viva,
                attribution=AttributionInput(agent_protocol="ucp"),
            )
        )

        assert checkout.order().pk != agents.pk

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
        ],
    )
    def test_one_that_ended(self, ended):
        checkout = _Checkout()
        earlier = checkout.order()
        Order.objects.filter(pk=earlier.pk).update(**ended)

        assert checkout.order().pk != earlier.pk

    def test_one_whose_payment_window_closed(self):
        """No link fits before its hold limit: the shopper could not pay
        it, so a new order is placed."""
        checkout = _Checkout()
        earlier = checkout.order()
        Order.objects.filter(pk=earlier.pk).update(
            created_at=earlier.created_at
            - payment_hold.max_hold_window()
            - timedelta(minutes=1)
        )

        assert checkout.order().pk != earlier.pk

    def test_an_offline_order_records_no_fingerprint(self):
        """Cash on delivery holds no payment link and takes the cart with
        it: there is nothing to resume."""
        checkout = _Checkout()
        cod = PayWayFactory(
            provider_code="cod",
            settlement=PaySettlement.COURIER_CASH,
            active=True,
            cost=Money(Decimal(0), "EUR"),
            free_threshold=Money(Decimal(0), "EUR"),
        )
        order = checkout.order(pay_way=cod)

        assert FINGERPRINT_KEY not in order.metadata


@pytest.mark.django_db(transaction=True)
class TestTheSameCheckoutSentTwiceAtOnce:
    """Real commits on real connections: the cart row lock serialises the
    two, and the second resumes the first's order."""

    def test_ends_with_one_order(self):
        checkout = _Checkout()

        placed, raised = race(lambda: checkout.order().pk)

        assert not raised, raised
        assert len(set(placed)) == 1
        assert Order.objects.count() == 1
        assert checkout.stock() == STOCK - 1, "the stock was held twice"


def test_the_last_units_held_by_the_order_still_resume_it():
    """The order took the last unit: the cart now asks for more than the
    stock holds, and the same checkout still resumes the order."""
    checkout = _Checkout()
    Product = type(checkout.product)
    Product.objects.filter(pk=checkout.product.pk).update(stock=1)
    earlier = checkout.order()
    assert checkout.order().pk == earlier.pk
