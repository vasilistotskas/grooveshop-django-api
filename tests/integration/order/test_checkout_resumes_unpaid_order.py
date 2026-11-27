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
from django.utils import timezone, translation
from djmoney.money import Money
from extra_settings.models import Setting

from cart.models.cart import Cart
from cart.models.item import CartItem
from country.factories import CountryFactory
from giftcard.factories import GiftCardFactory
from order.checkout import resume
from order.checkout.resume import FINGERPRINT_KEY
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import (
    CartNotReadyError,
    CartStockUnavailableError,
    InvalidGiftCardError,
    OrderCartChangedError,
    OrderTotalChangedError,
)
from order.inventory.stock import StockManager
from order.models.item import OrderItem
from order.models.order import AMOUNT_MISMATCH_FLAG, Order
from order.models.stock_reservation import StockReservation
from order.payments import payment_hold, stripe_settlement, viva_settlement
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from tests.utils.checkout import priced
from tests.utils.concurrency import (
    in_thread,
    race,
    wait_for_a_blocked_backend,
)
from tests.utils.legal import checkout_acceptance
from tests.utils.shipping import enable_rate
from tests.utils.viva import verified_payment
from user.factories.account import UserAccountFactory

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

    def request(self, *, pay_way=None, extra=None, **address) -> dict:
        return priced(
            legal_acceptance=self.acceptance,
            cart=self.cart,
            shipping_address=self.address(**address),
            pay_way=pay_way or self.viva,
            **(extra or {}),
        )

    def order(self, **choices) -> Order:
        return OrderService.create_order_from_cart(**self.request(**choices))

    def reserve(self) -> list:
        """What the storefront does before every order."""
        return StockManager.reserve_cart_stock(
            self.cart, {self.product.pk: self.item.quantity}
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

        recorded = order.metadata[FINGERPRINT_KEY]
        assert set(recorded) == {"checkout", "address", "lines"}
        assert len(recorded["checkout"]) == len(recorded["address"]) == 64
        assert recorded["lines"] == {str(checkout.product.pk): 1}

    def test_a_failed_attempt_still_resumes(self):
        """A failed payment does not end the order: its Viva payment
        order stays payable until its own expiry."""
        checkout = _Checkout()
        earlier = checkout.order()
        Order.objects.filter(pk=earlier.pk).update(
            payment_status=PaymentStatus.FAILED
        )

        assert checkout.order().pk == earlier.pk


class TestTheStorefrontReservesBeforeEveryOrder:
    def test_the_units_are_held_once(self):
        checkout = _Checkout()
        checkout.reserve()
        earlier = checkout.order()

        # Held as far as the stock allows, until the creation knows it is
        # the same order: the resume then releases it.
        [again] = checkout.reserve()
        assert again.quantity == 1
        assert checkout.order().pk == earlier.pk

        assert checkout.stock() == STOCK - 1
        assert not StockReservation.objects.filter(consumed=False).exists()

    def test_the_last_unit_resumes_too(self):
        checkout = _Checkout()
        type(checkout.product).objects.filter(pk=checkout.product.pk).update(
            stock=1
        )
        checkout.reserve()
        earlier = checkout.order()

        checkout.reserve()

        assert checkout.order().pk == earlier.pk

    def test_a_changed_checkout_is_held_in_full(self):
        """Nobody takes what it needs before it is created."""
        checkout = _Checkout()
        checkout.order()
        CartItem.objects.filter(pk=checkout.item.pk).update(quantity=3)
        checkout.item.refresh_from_db()

        [reservation] = checkout.reserve()

        assert reservation.quantity == 3
        with pytest.raises(CartStockUnavailableError):
            StockManager.reserve_cart_stock(
                Cart.objects.create(user=None),
                {checkout.product.pk: STOCK - 1 - 3 + 1},
            )

    def test_a_changed_checkout_needs_the_stock_left(self):
        """The earlier order keeps its units, so they are not the new
        one's: a change the stock left cannot cover is refused here."""
        checkout = _Checkout()
        type(checkout.product).objects.filter(pk=checkout.product.pk).update(
            stock=3
        )
        checkout.order()
        CartItem.objects.filter(pk=checkout.item.pk).update(quantity=3)
        checkout.item.refresh_from_db()

        with pytest.raises(CartStockUnavailableError):
            checkout.reserve()

    def test_a_changed_address_of_the_same_lines_is_still_held(self):
        """Only the creation knows it is a new order: until then the units
        it needs are held as far as the stock allows."""
        checkout = _Checkout()
        checkout.reserve()
        checkout.order()

        [reservation] = checkout.reserve()

        assert reservation.quantity == 1
        assert checkout.order(street="Stadiou").pk != (
            Order.objects.order_by("created_at").first().pk
        )
        assert checkout.stock() == STOCK - 2
        assert not StockReservation.objects.filter(consumed=False).exists()

    def test_a_repeat_holds_what_is_left_of_its_lines(self):
        checkout = _Checkout()
        CartItem.objects.filter(pk=checkout.item.pk).update(quantity=2)
        checkout.item.refresh_from_db()
        type(checkout.product).objects.filter(pk=checkout.product.pk).update(
            stock=3
        )
        checkout.reserve()
        earlier = checkout.order()

        [reservation] = checkout.reserve()

        assert reservation.quantity == 1
        assert checkout.order().pk == earlier.pk
        assert not StockReservation.objects.filter(consumed=False).exists()

    def test_a_promotion_gift_does_not_hide_the_repeat(self):
        """A gift line the order got at creation is not one of its lines."""
        checkout = _Checkout()
        type(checkout.product).objects.filter(pk=checkout.product.pk).update(
            stock=1
        )
        checkout.reserve()
        earlier = checkout.order()
        OrderItem.objects.create(
            order=earlier,
            product=ProductFactory(stock=5),
            quantity=1,
            price=Money(0, "EUR"),
            vat_rate=0,
        )

        assert checkout.reserve() == []
        assert checkout.order().pk == earlier.pk

    def test_a_hold_left_from_before_is_released(self):
        """A hold made while the order was not yet there (another tab)."""
        checkout = _Checkout()
        earlier = checkout.order()
        StockManager.reserve_stock(
            product_id=checkout.product.pk,
            quantity=1,
            session_id=str(checkout.cart.uuid),
            user_id=None,
        )

        assert checkout.order().pk == earlier.pk

        assert not StockReservation.objects.filter(consumed=False).exists()

    def test_another_cart_is_still_excluded(self):
        checkout = _Checkout()
        type(checkout.product).objects.filter(pk=checkout.product.pk).update(
            stock=1
        )
        checkout.order()

        with pytest.raises(CartStockUnavailableError):
            StockManager.reserve_cart_stock(
                Cart.objects.create(user=None), {checkout.product.pk: 1}
            )


class TestTheOrderTakesTheRepeatsOwnAnswers:
    def test_its_consent_and_browser_data(self):
        checkout = _Checkout()
        checkout.order(
            extra={
                "meta_context": {"fbp": "fb.1.1.1", "consent": {"ads": True}}
            }
        )

        again = checkout.order(extra={"meta_context": None})

        again.refresh_from_db()
        assert "meta" not in again.metadata

    def test_its_language(self):
        checkout = _Checkout()
        with translation.override("el"):
            earlier = checkout.order()
        assert earlier.language_code == "el"

        with translation.override("en"):
            again = checkout.order()

        again.refresh_from_db()
        assert (again.pk, again.language_code) == (earlier.pk, "en")

    def test_a_legal_text_is_recorded_once(self):
        checkout = _Checkout()
        earlier = checkout.order()
        recorded = earlier.legal_acceptances.count()

        checkout.order()

        assert earlier.legal_acceptances.count() == recorded


class TestAPaymentBeingDecided:
    """Money is being taken, or was: the repeat gets that order, whose link
    answers that a payment is in progress, never a second one to pay."""

    @pytest.mark.parametrize("window_closed", [False, True])
    @pytest.mark.parametrize("decided", ["processing", "amount mismatch"])
    def test_the_order_is_returned(self, decided, window_closed):
        checkout = _Checkout()
        earlier = checkout.order()
        if decided == "processing":
            earlier.payment_status = PaymentStatus.PROCESSING
        else:
            earlier.metadata[AMOUNT_MISMATCH_FLAG] = {"expected": "1"}
        earlier.save(update_fields=["payment_status", "metadata"])
        if window_closed:
            Order.objects.filter(pk=earlier.pk).update(
                created_at=earlier.created_at
                - payment_hold.max_hold_window()
                - timedelta(minutes=1)
            )

        assert checkout.order().pk == earlier.pk
        assert Order.objects.count() == 1

    def test_one_paid_while_the_repeat_waited_for_its_lock(self, monkeypatch):
        checkout = _Checkout()
        earlier = checkout.order()
        real = resume.unpaid_orders_of_cart

        def paid_meanwhile(cart, **kwargs):
            """The candidates are read, then the payment commits."""
            found = real(cart, **kwargs)
            rows = list(found.values_list("pk", flat=True))
            Order.objects.filter(pk__in=rows).update(
                payment_status=PaymentStatus.COMPLETED
            )
            return Order.objects.filter(pk__in=rows)

        monkeypatch.setattr(resume, "unpaid_orders_of_cart", paid_meanwhile)

        assert checkout.order().pk == earlier.pk
        assert Order.objects.count() == 1


class TestAChangedCheckoutPlacesAnotherOrder:
    def test_going_back_to_the_first_of_two_resumes_it(self):
        checkout = _Checkout()
        first = checkout.order()
        second = checkout.order(street_number="2")
        assert second.pk != first.pk

        assert checkout.order().pk == first.pk
        assert Order.objects.count() == 2

    @pytest.mark.parametrize("edit", [{"street": "Stadiou"}, {"floor": "2"}])
    def test_an_order_whose_address_was_edited_is_not_the_same(self, edit):
        checkout = _Checkout()
        earlier = checkout.order()
        Order.objects.filter(pk=earlier.pk).update(**edit)

        assert checkout.order().pk != earlier.pk

    def test_one_that_spent_a_gift_card(self, monkeypatch):
        """The card's balance went into the first order: the second quote
        refuses it, so the checkout is not resumed, nor charged twice."""
        checkout = _Checkout()
        real = Setting.get
        enabled = {"GIFT_CARDS_ENABLED": True, "GIFT_CARD_VALIDITY_DAYS": 1825}
        monkeypatch.setattr(
            Setting,
            "get",
            lambda name, default=None: enabled.get(name, real(name, default)),
        )
        card = GiftCardFactory(initial_value=Money(Decimal("5.00"), "EUR"))
        # The same request twice, the total the client was first shown with it.
        request = checkout.request(extra={"gift_card_codes": [card.code]})
        OrderService.create_order_from_cart(**request)

        with pytest.raises(InvalidGiftCardError):
            OrderService.create_order_from_cart(**request)

        assert Order.objects.count() == 1

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
        agents = OrderService.create_order_from_cart(
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

    def test_one_whose_open_link_outlives_the_window(self):
        """Its link stays payable until the expiry the provider applied."""
        checkout = _Checkout()
        earlier = checkout.order()
        Order.objects.filter(pk=earlier.pk).update(
            created_at=earlier.created_at
            - payment_hold.max_hold_window()
            - timedelta(minutes=1),
            payment_hold_expires_at=timezone.now() + timedelta(minutes=2),
        )

        assert checkout.order().pk == earlier.pk

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


@pytest.mark.django_db(transaction=True)
class TestSettlementAndCheckoutNeverCreateASecondOrder:
    """Real commits on real connections (#839): payment settlement locks the
    cart before the order and clears the order's lines inside the same
    transaction, so a checkout landing beside settlement either resumes the
    existing order or sees the cleared cart — never a second order and never
    a deadlock."""

    @staticmethod
    def _barrier_race(first, second):
        """Start two distinct callables on two real DB connections at once."""
        import threading

        barrier = threading.Barrier(2)
        outcomes: list[tuple[str, object]] = []
        failures: list[BaseException] = []

        def run(name, fn):
            barrier.wait(timeout=30)
            outcomes.append((name, fn()))

        w1 = in_thread(lambda: run("settle", first), failures)
        w2 = in_thread(lambda: run("checkout", second), failures)
        w1.finish(timeout=60)
        w2.finish(timeout=60)
        return dict(outcomes), failures

    @pytest.mark.parametrize("authenticated", [False, True])
    def test_stripe_payment_intent_race_with_repeat_checkout(
        self, authenticated
    ):
        checkout = _Checkout()
        user = UserAccountFactory(num_addresses=0) if authenticated else None
        if user is not None:
            Cart.objects.filter(pk=checkout.cart.pk).update(user=user)
            checkout.cart.refresh_from_db()
        stripe_pw = PayWayFactory.create_online_payment("stripe")
        earlier = checkout.order(pay_way=stripe_pw, extra={"user": user})
        Order.objects.filter(pk=earlier.pk).update(
            payment_id="pi_race_settle_1"
        )
        total = earlier.calculate_order_total_amount()
        # Capture the checkout request before settlement clears the cart,
        # matching a shopper whose checkout POST arrived right beside the
        # webhook.
        repeat_req = checkout.request(pay_way=stripe_pw, extra={"user": user})

        outcomes, failures = self._barrier_race(
            lambda: OrderService.handle_payment_succeeded(
                "pi_race_settle_1", captured=total
            ),
            lambda: OrderService.create_order_from_cart(**repeat_req).pk,
        )

        # Checkout either resumed the existing order or was refused cleanly
        # because settlement already deleted the cart.
        if failures:
            assert len(failures) == 1
            assert isinstance(failures[0], CartNotReadyError), failures
        else:
            assert outcomes["checkout"] == earlier.pk
        assert Order.objects.count() == 1
        assert checkout.stock() == STOCK - 1
        assert not Cart.objects.filter(pk=checkout.cart.pk).exists()

    def test_stripe_checkout_session_race_with_repeat_checkout(self):
        checkout = _Checkout()
        stripe_pw = PayWayFactory.create_online_payment("stripe")
        earlier = checkout.order(pay_way=stripe_pw)
        total = earlier.calculate_order_total_amount()
        repeat_req = checkout.request(pay_way=stripe_pw)

        outcomes, failures = self._barrier_race(
            lambda: stripe_settlement.settle_checkout_session(
                earlier.pk,
                session_id="cs_race_1",
                payment_intent_id="pi_race_cs_1",
                captured=total,
            ),
            lambda: OrderService.create_order_from_cart(**repeat_req).pk,
        )

        if failures:
            assert len(failures) == 1
            assert isinstance(failures[0], CartNotReadyError), failures
        else:
            assert outcomes["checkout"] == earlier.pk
        assert Order.objects.count() == 1
        assert checkout.stock() == STOCK - 1
        assert not Cart.objects.filter(pk=checkout.cart.pk).exists()

    def test_viva_settlement_race_with_repeat_checkout(self, monkeypatch):
        checkout = _Checkout()
        earlier = checkout.order()
        earlier.metadata["viva_order_codes"] = ["OC_RACE_1"]
        earlier.save(update_fields=["metadata"])
        repeat_req = checkout.request()
        monkeypatch.setattr(
            viva_settlement,
            "verify_transaction",
            lambda _tid: (
                PaymentStatus.COMPLETED,
                verified_payment(earlier, "OC_RACE_1"),
            ),
        )

        outcomes, failures = self._barrier_race(
            lambda: viva_settlement.apply_event(
                earlier,
                event_type_id=viva_settlement.VIVA_TRANSACTION_PAYMENT_CREATED,
                event_data={"StatusId": "F"},
                transaction_id="viva_txn_race_1",
                order_code="OC_RACE_1",
                status_id="F",
                handle=viva_settlement.handle_payment_created,
                read=viva_settlement.read_payment_created,
            ),
            lambda: OrderService.create_order_from_cart(**repeat_req).pk,
        )

        if failures:
            assert len(failures) == 1
            assert isinstance(failures[0], CartNotReadyError), failures
        else:
            assert outcomes["checkout"] == earlier.pk
        assert Order.objects.count() == 1
        assert checkout.stock() == STOCK - 1
        assert not Cart.objects.filter(pk=checkout.cart.pk).exists()

    def test_checkout_waiting_on_settlement_sees_deleted_cart(self):
        """Deterministic order: settlement holds the cart lock while checkout
        waits for it; when settlement commits, checkout sees the cart gone
        and creates no second order."""
        import threading

        from django.db import transaction

        checkout = _Checkout()
        stripe_pw = PayWayFactory.create_online_payment("stripe")
        earlier = checkout.order(pay_way=stripe_pw)
        Order.objects.filter(pk=earlier.pk).update(
            payment_id="pi_ordered_wait_1"
        )
        total = earlier.calculate_order_total_amount()
        repeat_req = checkout.request(pay_way=stripe_pw)

        settled_uncommitted = threading.Event()
        commit_now = threading.Event()
        settle_failures: list[BaseException] = []
        checkout_failures: list[BaseException] = []

        def hold_settlement() -> None:
            with transaction.atomic():
                OrderService.handle_payment_succeeded(
                    "pi_ordered_wait_1", captured=total
                )
                settled_uncommitted.set()
                assert commit_now.wait(timeout=30)

        settler = in_thread(hold_settlement, settle_failures)
        assert settled_uncommitted.wait(timeout=30)

        buyer = in_thread(
            lambda: OrderService.create_order_from_cart(**repeat_req),
            checkout_failures,
        )
        wait_for_a_blocked_backend(timeout=15)
        commit_now.set()

        settler.finish(timeout=30)
        buyer.finish(timeout=30)
        assert not settle_failures, settle_failures
        assert len(checkout_failures) == 1
        assert isinstance(checkout_failures[0], CartNotReadyError), (
            checkout_failures
        )
        assert Order.objects.count() == 1
        assert checkout.stock() == STOCK - 1

    def test_checkout_with_extra_item_never_reorders_paid_line(self):
        """The shopper added a second product while paying; settlement removes
        the paid product inside its transaction, so a waiting checkout sees
        only the new product and is refused for the changed cart."""
        import threading

        from django.db import transaction

        checkout = _Checkout()
        stripe_pw = PayWayFactory.create_online_payment("stripe")
        earlier = checkout.order(pay_way=stripe_pw)
        Order.objects.filter(pk=earlier.pk).update(
            payment_id="pi_extra_item_wait_1"
        )
        total = earlier.calculate_order_total_amount()

        extra_product = ProductFactory.create(
            stock=STOCK,
            price=Money(Decimal("15.00"), "EUR"),
            discount_percent=Decimal(0),
            active=True,
            num_images=0,
            num_reviews=0,
        )
        CartItem.objects.create(
            cart=checkout.cart, product=extra_product, quantity=1
        )
        # Quote before settlement commits (covers both products).
        repeat_req = checkout.request(pay_way=stripe_pw)

        settled_uncommitted = threading.Event()
        commit_now = threading.Event()
        settle_failures: list[BaseException] = []
        checkout_failures: list[BaseException] = []

        def hold_settlement() -> None:
            with transaction.atomic():
                OrderService.handle_payment_succeeded(
                    "pi_extra_item_wait_1", captured=total
                )
                settled_uncommitted.set()
                assert commit_now.wait(timeout=30)

        settler = in_thread(hold_settlement, settle_failures)
        assert settled_uncommitted.wait(timeout=30)

        buyer = in_thread(
            lambda: OrderService.create_order_from_cart(**repeat_req),
            checkout_failures,
        )
        wait_for_a_blocked_backend(timeout=15)
        commit_now.set()

        settler.finish(timeout=30)
        buyer.finish(timeout=30)
        assert not settle_failures, settle_failures
        assert len(checkout_failures) == 1
        assert isinstance(
            checkout_failures[0],
            (OrderCartChangedError, OrderTotalChangedError),
        ), checkout_failures
        assert Order.objects.count() == 1
        assert list(
            checkout.cart.items.values_list("product_id", flat=True)
        ) == [extra_product.pk]
