"""One payment intent, one order.

``create_order_from_cart`` used to create an order for every call, so a
repeated submit of one checkout (a double click, a retry after a timeout,
Back and resubmit while the hosted payment is still open) produced a second
order on the same ``payment_intent_id``. Both took the stock, only one got
its webhook, and the other sat PENDING until the stuck-order sweep.

What is pinned here:

* a repeat answers with the first order and takes the stock once;
* it does so even when the first order took the last units, which is
  exactly when a repeat would otherwise fail cart validation;
* an intent that belongs to a DIFFERENT cart, or to an order that is
  canceled or deleted, is refused rather than answered or re-used;
* ``Order.payment_id`` is unique in the database, while "no payment id
  yet" (``''`` and NULL) stays repeatable;
* two submits racing for the same cart serialise on the cart lock and end
  with one order.
"""

import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from djmoney.money import Money

from cart.models.cart import Cart
from cart.models.item import CartItem
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentVerificationError
from order.models.order import Order
from order.models.stock_log import StockLog
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = [
    pytest.mark.assert_english,
    pytest.mark.usefixtures("trust_intent_binding"),
]

PAYMENT_INTENT_ID = "pi_dup_123"


def _eur(amount: str) -> Money:
    return Money(amount, settings.DEFAULT_CURRENCY)


def _provider_reporting(status: PaymentStatus) -> Mock:
    provider = Mock()
    provider.get_payment_status.return_value = (status, {})
    return provider


def _pending_order(payment_id) -> Order:
    return Order.objects.create(
        user=None,
        payment_id=payment_id,
        payment_status=PaymentStatus.PENDING,
        status=OrderStatus.PENDING,
        email="test@example.com",
        first_name="John",
        last_name="Doe",
        street="Main St",
        street_number="123",
        city="Athens",
        zipcode="12345",
        phone="+306900000000",
        shipping_price=_eur("5.00"),
        amount_due=_eur("0.00"),
    )


def _shipping_address(country) -> dict:
    return {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john@example.com",
        "street": "Main St",
        "street_number": "123",
        "city": "Athens",
        "zipcode": "12345",
        "country_id": country.alpha_2,
        "phone": "+306900000000",
        "shipping_kind": "home_delivery",
    }


class _Checkout:
    """A signed-in shopper's cart (2 x product1, 1 x product2) and the
    hosted-payment pay way a Stripe intent is created against."""

    def setup_method(self):
        from country.factories import CountryFactory
        from tests.utils.shipping import enable_rate

        self.user = UserAccountFactory.create(num_addresses=0)
        # ONLINE and not yet paid: the cart is kept while the shopper
        # still owes the money, which is the state a repeat submit finds.
        self.pay_way = PayWayFactory.create(
            provider_code="stripe", settlement=PaySettlement.ONLINE
        )
        self.country = CountryFactory.create()
        enable_rate(self.country)

        self.product1 = ProductFactory.create(
            price=_eur("50.00"), stock=20, active=True
        )
        self.product2 = ProductFactory.create(
            price=_eur("30.00"), stock=15, active=True
        )
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(
            cart=self.cart, product=self.product1, quantity=2
        )
        CartItem.objects.create(
            cart=self.cart, product=self.product2, quantity=1
        )

    def _create(self, **overrides):
        kwargs = {
            "cart": self.cart,
            "shipping_address": _shipping_address(self.country),
            "payment_intent_id": PAYMENT_INTENT_ID,
            "pay_way": self.pay_way,
            "user": self.user,
        }
        kwargs.update(overrides)
        return OrderService.create_order_from_cart(**kwargs)

    def _stock(self):
        self.product1.refresh_from_db()
        self.product2.refresh_from_db()
        return self.product1.stock, self.product2.stock


@pytest.mark.django_db
class TestARepeatSubmitReturnsTheFirstOrder(_Checkout):
    @patch("order.payment.get_payment_provider")
    def test_one_order_and_one_stock_decrement(self, mock_get_provider):
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )

        first = self._create()
        second = self._create()

        assert second.pk == first.pk
        assert Order.objects.filter(payment_id=PAYMENT_INTENT_ID).count() == 1
        assert self._stock() == (18, 14)
        assert (
            StockLog.objects.filter(
                order=first, operation_type=StockLog.OPERATION_DECREMENT
            ).count()
            == 2
        ), "each product is decremented once, for the one order"
        assert first.items.count() == 2

    @patch("order.payment.get_payment_provider")
    def test_a_repeat_is_answered_when_the_first_order_took_the_last_units(
        self, mock_get_provider
    ):
        """Cart validation counts the physical stock, which the first
        order has already emptied: a repeat that validated first would be
        refused for exactly what the first submit did."""
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )
        self.product1.stock = 2
        self.product1.save()
        self.product2.stock = 1
        self.product2.save()

        first = self._create()
        assert self._stock() == (0, 0)

        second = self._create()

        assert second.pk == first.pk
        assert self._stock() == (0, 0)

    @patch("order.payment.get_payment_provider")
    def test_a_repeat_does_not_send_a_second_confirmation(
        self, mock_get_provider
    ):
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )
        self._create()

        with patch(
            "order.signals.handlers.send_admin_new_order_email.delay"
        ) as admin_email:
            self._create()

        admin_email.assert_not_called()

    @patch("order.payment.get_payment_provider")
    def test_a_repeat_is_answered_without_asking_the_provider(
        self, mock_get_provider
    ):
        """The order exists, so its answer cannot depend on the provider
        being reachable: a retry after a timeout is exactly when it may
        not be."""
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )
        first = self._create()

        mock_get_provider.reset_mock()
        provider = Mock()
        provider.get_payment_status.side_effect = ConnectionError(
            "the provider is unreachable"
        )
        mock_get_provider.return_value = provider

        second = self._create()

        assert second.pk == first.pk
        mock_get_provider.assert_not_called()
        provider.get_payment_status.assert_not_called()

    @patch("order.payment.get_payment_provider")
    def test_the_locked_lookup_catches_a_first_submit_that_raced_the_other(
        self, mock_get_provider
    ):
        """Two first submits both miss the lock-free lookup (neither order
        exists yet); the one that takes the cart lock second finds the
        other's committed order under it. The lock-free miss is simulated:
        the second call's first lookup answers "none"."""
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )
        first = self._create()
        real_lookup = OrderService._order_created_for_payment
        lookups = []

        def miss_once_then_look(payment_intent_id, cart):
            lookups.append(payment_intent_id)
            if len(lookups) == 1:
                return None
            return real_lookup(payment_intent_id, cart)

        with patch.object(
            OrderService,
            "_order_created_for_payment",
            side_effect=miss_once_then_look,
        ):
            second = self._create()

        assert len(lookups) == 2, "the lookup under the cart lock never ran"
        assert second.pk == first.pk
        assert Order.objects.filter(payment_id=PAYMENT_INTENT_ID).count() == 1
        assert self._stock() == (18, 14)


@pytest.mark.django_db
class TestAnIntentThatIsNotThisCheckoutsIsRefused(_Checkout):
    @patch("order.payment.get_payment_provider")
    def test_another_carts_order_is_not_handed_out(self, mock_get_provider):
        """Answering with it would show one shopper another's order."""
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )
        first = self._create()
        other_cart = Cart.objects.create(user=None)
        CartItem.objects.create(
            cart=other_cart, product=self.product1, quantity=1
        )

        with pytest.raises(PaymentVerificationError, match="already belongs"):
            self._create(cart=other_cart, user=None)

        assert list(
            Order.objects.filter(payment_id=PAYMENT_INTENT_ID).values_list(
                "pk", flat=True
            )
        ) == [first.pk]
        assert self._stock() == (18, 14)

    @patch("order.payment.get_payment_provider")
    def test_a_canceled_orders_intent_is_not_reused(self, mock_get_provider):
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )
        first = self._create()
        Order.objects.filter(pk=first.pk).update(status=OrderStatus.CANCELED)

        with pytest.raises(PaymentVerificationError, match="already belongs"):
            self._create()

        assert Order.objects.filter(payment_id=PAYMENT_INTENT_ID).count() == 1

    @patch("order.payment.get_payment_provider")
    def test_a_deleted_orders_intent_is_not_reused(self, mock_get_provider):
        """The unique index covers soft-deleted rows too."""
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )
        first = self._create()
        Order.objects.filter(pk=first.pk).update(is_deleted=True)

        with pytest.raises(PaymentVerificationError, match="already belongs"):
            self._create()

        assert (
            Order.objects.all_with_deleted()
            .filter(payment_id=PAYMENT_INTENT_ID)
            .count()
            == 1
        )

    @patch("order.payment.get_payment_provider")
    def test_two_carts_racing_for_one_intent_are_answered_like_the_sequential_case(
        self, mock_get_provider
    ):
        """The cart lock only serialises submits of ONE cart. Two carts
        can both miss the lookup (neither order exists yet), and then the
        unique index rejects the second insert. That must come out as the
        sequential case does (``payment_verification``), not as a generic
        ``invalid_order_data``. The race is simulated by blinding the
        lookups; the index and the insert are real."""
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )
        other_cart = Cart.objects.create(user=None)
        CartItem.objects.create(
            cart=other_cart, product=self.product1, quantity=1
        )

        with patch.object(
            OrderService, "_order_created_for_payment", return_value=None
        ):
            first = self._create()
            with pytest.raises(PaymentVerificationError) as caught:
                self._create(cart=other_cart, user=None)

        assert isinstance(caught.value.__cause__, IntegrityError)
        assert list(
            Order.objects.filter(payment_id=PAYMENT_INTENT_ID).values_list(
                "pk", flat=True
            )
        ) == [first.pk]
        assert self._stock() == (18, 14), (
            "the refused order must not keep the stock it took"
        )

    @pytest.mark.parametrize(
        "cause",
        [None, "some_other_constraint"],
        ids=["no driver diagnostics", "another constraint"],
    )
    @patch("order.payment.get_payment_provider")
    def test_any_other_integrity_error_is_still_invalid_order_data(
        self, mock_get_provider, cause
    ):
        """Only the ``payment_id`` index means "this payment is taken":
        every other integrity failure keeps its generic answer."""
        from order.exceptions import InvalidOrderDataError

        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )
        error = IntegrityError("some other integrity failure")
        if cause is not None:
            driver_error = Exception("the driver's error")
            driver_error.diag = SimpleNamespace(constraint_name=cause)
            error.__cause__ = driver_error

        with (
            patch.object(Order.objects, "create", side_effect=error),
            pytest.raises(InvalidOrderDataError),
        ):
            self._create()


@pytest.mark.django_db
class TestPaymentIdIsUniqueInTheDatabase:
    def test_a_second_order_on_one_payment_id_is_refused(self):
        _pending_order(PAYMENT_INTENT_ID)

        with pytest.raises(IntegrityError), transaction.atomic():
            _pending_order(PAYMENT_INTENT_ID)

    @pytest.mark.parametrize("no_payment_yet", ["", None])
    def test_orders_with_no_payment_yet_may_repeat(self, no_payment_yet):
        _pending_order(no_payment_yet)
        _pending_order(no_payment_yet)

        assert Order.objects.filter(payment_id=no_payment_yet).count() == 2


@pytest.mark.django_db(transaction=True)
class TestRacingSubmitsEndWithOneOrder(_Checkout):
    """Real commits on real connections: the cart row lock is the point."""

    @patch("order.payment.get_payment_provider")
    def test_two_threads_one_cart_one_intent(self, mock_get_provider):
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )
        self.setup_method()
        barrier = threading.Barrier(2)
        order_ids: list[int] = []
        failures: list[BaseException] = []

        def submit():
            try:
                barrier.wait(timeout=30)
                order_ids.append(self._create().pk)
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
        assert len(order_ids) == 2 and len(set(order_ids)) == 1, order_ids
        assert Order.objects.filter(payment_id=PAYMENT_INTENT_ID).count() == 1
        assert self._stock() == (18, 14), "the stock was taken twice"
