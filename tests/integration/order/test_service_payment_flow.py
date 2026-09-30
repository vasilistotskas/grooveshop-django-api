from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from django.utils import timezone
from djmoney.money import Money

from cart.models.cart import Cart
from cart.models.item import CartItem
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import (
    CartNotReadyError,
    InvalidOrderDataError,
    PaymentVerificationError,
)
from order.models.item import OrderItem
from order.models.order import Order
from order.models.stock_reservation import StockReservation
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.assert_english


def _eur(amount: str) -> Money:
    return Money(amount, settings.DEFAULT_CURRENCY)


def _provider_reporting(status: PaymentStatus) -> Mock:
    provider = Mock()
    provider.get_payment_status.return_value = (status, {"status": "x"})
    return provider


def _pending_stripe_order(pay_way, payment_id: str, **overrides) -> Order:
    fields = {
        "user": None,
        "pay_way": pay_way,
        "payment_id": payment_id,
        "payment_status": PaymentStatus.PENDING,
        "status": OrderStatus.PENDING,
        "email": "test@example.com",
        "first_name": "John",
        "last_name": "Doe",
        "street": "Main St",
        "street_number": "123",
        "city": "Athens",
        "zipcode": "12345",
        "phone": "+306900000000",
        "shipping_price": _eur("5.00"),
        "paid_amount": _eur("0.00"),
    }
    fields.update(overrides)
    return Order.objects.create(**fields)


@pytest.mark.django_db
class TestCreateOrderFromCart:
    def setup_method(self):
        from country.factories import CountryFactory
        from tests.utils.shipping import enable_rate

        self.user = UserAccountFactory.create(num_addresses=0)
        self.pay_way = PayWayFactory.create(provider_code="stripe")
        self.country = CountryFactory.create()
        # A ShippingRate is per-country — this ad-hoc country has none
        # until this call, and ``shipping_cost`` would refuse the order.
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

        self.shipping_address = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": self.country.alpha_2,
            "phone": "+306900000000",
            "shipping_kind": "home_delivery",
        }
        self.payment_intent_id = "pi_test123abc"

    def _create(self, **overrides):
        kwargs = {
            "cart": self.cart,
            "shipping_address": self.shipping_address,
            "payment_intent_id": self.payment_intent_id,
            "pay_way": self.pay_way,
            "user": self.user,
        }
        kwargs.update(overrides)
        return OrderService.create_order_from_cart(**kwargs)

    @patch("order.payment.get_payment_provider")
    def test_creates_a_pending_order_from_the_cart(self, mock_get_provider):
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.COMPLETED
        )

        order = self._create()

        assert order.status == OrderStatus.PENDING
        assert order.payment_id == self.payment_intent_id
        assert order.user == self.user
        assert order.pay_way == self.pay_way
        assert (order.first_name, order.email, order.city) == (
            "John",
            "john@example.com",
            "Athens",
        )
        assert dict(order.items.values_list("product_id", "quantity")) == {
            self.product1.id: 2,
            self.product2.id: 1,
        }
        mock_get_provider.return_value.get_payment_status.assert_called_once_with(
            self.payment_intent_id
        )

    @pytest.mark.parametrize("payment_intent_id", [None, ""])
    def test_missing_payment_intent_id_is_refused(self, payment_intent_id):
        with pytest.raises(
            InvalidOrderDataError, match="Payment intent ID is required"
        ):
            self._create(payment_intent_id=payment_intent_id)

        assert not Order.objects.filter(user=self.user).exists()

    def test_incomplete_shipping_address_is_refused(self):
        with pytest.raises(InvalidOrderDataError, match="last_name"):
            self._create(shipping_address={"first_name": "John"})

        assert not Order.objects.filter(user=self.user).exists()

    @patch("order.payment.get_payment_provider")
    def test_failed_payment_intent_is_refused(self, mock_get_provider):
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.FAILED
        )

        with pytest.raises(PaymentVerificationError) as exc_info:
            self._create(payment_intent_id="pi_invalid123")

        assert "invalid state" in str(exc_info.value).lower()
        assert "FAILED" in str(exc_info.value)

    @patch("order.payment.get_payment_provider")
    def test_insufficient_stock_is_refused_before_payment_lookup(
        self, mock_get_provider
    ):
        scarce = ProductFactory.create(
            price=_eur("10.00"), stock=1, active=True
        )
        CartItem.objects.create(cart=self.cart, product=scarce, quantity=5)

        with pytest.raises(CartNotReadyError) as exc_info:
            self._create()

        assert exc_info.value.insufficient_stock is True
        mock_get_provider.assert_not_called()
        scarce.refresh_from_db()
        assert scarce.stock == 1

    @patch("order.payment.get_payment_provider")
    def test_converts_the_carts_reservations_into_the_sale(
        self, mock_get_provider
    ):
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.COMPLETED
        )
        reservations = [
            StockReservation.objects.create(
                product=product,
                quantity=quantity,
                session_id=str(self.cart.uuid),
                reserved_by=self.user,
                expires_at=timezone.now() + timedelta(minutes=15),
            )
            for product, quantity in ((self.product1, 2), (self.product2, 1))
        ]

        order = self._create()

        for reservation in reservations:
            reservation.refresh_from_db()
            assert reservation.consumed is True
            assert reservation.order == order
        assert sorted(order.metadata["stock_reservation_ids"]) == sorted(
            r.id for r in reservations
        )
        # Decremented once, by the conversion — the OrderItem post-save
        # handler deliberately does not decrement on create.
        self.product1.refresh_from_db()
        self.product2.refresh_from_db()
        assert (self.product1.stock, self.product2.stock) == (18, 14)

    @patch("order.payment.get_payment_provider")
    def test_without_reservations_decrements_stock_directly(
        self, mock_get_provider
    ):
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.COMPLETED
        )

        order = self._create()

        assert order.metadata.get("stock_reservation_ids", []) == []
        self.product1.refresh_from_db()
        self.product2.refresh_from_db()
        assert (self.product1.stock, self.product2.stock) == (18, 14)

    @patch("order.payment.get_payment_provider")
    def test_cart_clears_at_creation_for_an_offline_pay_way(
        self, mock_get_provider
    ):
        """Pins ``settlement``: the factory randomises it, and cart
        clearing depends on whether the shopper still owes money online
        (hosted flows keep the cart until ``order_paid``)."""
        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.COMPLETED
        )
        offline_pay_way = PayWayFactory.create(
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH,
        )

        order = self._create(pay_way=offline_pay_way)

        assert self.cart.items.count() == 0
        assert order.items.count() == 2

    @patch("order.payment.get_payment_provider")
    def test_cart_survives_until_a_hosted_payment_completes(
        self, mock_get_provider
    ):
        """Viva and Stripe mint the Order first and then redirect
        off-site. Clearing at creation meant abandoning that page — or
        pressing Back — silently emptied the shopper's basket and
        stranded a PENDING order (2026-08-08: 35 Viva orders ended
        CANCELED with payment PENDING against 27 COMPLETED).
        ``order_paid`` is what clears it."""
        from order.signals import order_paid

        mock_get_provider.return_value = _provider_reporting(
            PaymentStatus.PENDING
        )
        hosted_pay_way = PayWayFactory.create(
            provider_code="viva_wallet", settlement=PaySettlement.ONLINE
        )

        order = self._create(pay_way=hosted_pay_way)

        assert not order.is_paid
        assert self.cart.items.count() == 2, (
            "cart was destroyed before the shopper paid"
        )

        order_paid.send(sender=type(order), order=order)

        assert self.cart.items.count() == 0


@pytest.mark.django_db
class TestHandlePaymentSucceeded:
    def setup_method(self):
        self.pay_way = PayWayFactory.create(provider_code="stripe")
        self.payment_intent_id = "pi_test123abc"
        self.order = _pending_stripe_order(
            self.pay_way,
            self.payment_intent_id,
            user=UserAccountFactory.create(num_addresses=0),
        )

    @patch("order.signals.handlers.send_order_status_update_email.delay")
    def test_marks_a_pending_order_paid_and_processing(self, mock_status_email):
        OrderItem.objects.create(
            order=self.order,
            product=ProductFactory.create(price=_eur("100.00"), stock=10),
            quantity=2,
            price=_eur("100.00"),
        )

        outcome = OrderService.handle_payment_succeeded(self.payment_intent_id)

        assert outcome.applied is True
        assert outcome.previous_payment_status == PaymentStatus.PENDING
        result = outcome.order
        assert result.id == self.order.id
        result.refresh_from_db()
        assert result.status == OrderStatus.PROCESSING
        assert result.payment_status == PaymentStatus.COMPLETED
        assert result.payment_method == "stripe"
        # 2 x 100 + 5 shipping
        assert result.paid_amount == _eur("205.00")
        assert result.status_updated_at is not None
        # PROCESSING is internal: the webhook's order-confirmation email
        # covers it, so no generic status-update email is sent too.
        mock_status_email.assert_not_called()

    def test_unknown_payment_id_returns_none(self):
        with patch("order.services.logger") as mock_logger:
            result = OrderService.handle_payment_succeeded("pi_nonexistent")

        assert result is None
        mock_logger.error.assert_called_with(
            "Order not found for payment_intent: %s", "pi_nonexistent"
        )

    def test_leaves_a_non_pending_order_status_alone(self):
        self.order.status = OrderStatus.PROCESSING
        self.order.save()

        outcome = OrderService.handle_payment_succeeded(self.payment_intent_id)

        assert outcome.applied is True
        assert outcome.order.status == OrderStatus.PROCESSING
        assert outcome.order.payment_status == PaymentStatus.COMPLETED

    def test_a_repeated_event_is_idempotent(self):
        OrderService.handle_payment_succeeded(self.payment_intent_id)
        self.order.refresh_from_db()
        first_updated_at = self.order.status_updated_at

        outcome = OrderService.handle_payment_succeeded(self.payment_intent_id)

        # Nothing left to confirm: no second history row or email.
        assert outcome.applied is False
        assert outcome.previous_payment_status == PaymentStatus.COMPLETED
        assert outcome.order.id == self.order.id
        self.order.refresh_from_db()
        assert self.order.status == OrderStatus.PROCESSING
        assert self.order.payment_status == PaymentStatus.COMPLETED
        assert self.order.status_updated_at == first_updated_at

    @pytest.mark.parametrize(
        "settled_payment_status",
        [
            PaymentStatus.REFUNDED,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.CANCELED,
        ],
    )
    def test_does_not_regress_a_settled_payment_status(
        self, settled_payment_status
    ):
        """Stripe does not guarantee event order: a delayed 'succeeded'
        can arrive after 'charge.refunded' moved the order to REFUNDED."""
        order = _pending_stripe_order(
            self.pay_way,
            "pi_settled",
            payment_status=settled_payment_status,
            status=OrderStatus.DELIVERED,
            paid_amount=_eur("55.00"),
        )

        outcome = OrderService.handle_payment_succeeded("pi_settled")

        assert outcome.applied is False
        assert outcome.previous_payment_status == settled_payment_status
        assert outcome.order.id == order.id
        order.refresh_from_db()
        assert order.payment_status == settled_payment_status
        assert order.status == OrderStatus.DELIVERED


@pytest.mark.django_db
class TestHandlePaymentFailed:
    def setup_method(self):
        self.pay_way = PayWayFactory.create(provider_code="stripe")
        self.payment_intent_id = "pi_test123abc"
        self.order = _pending_stripe_order(self.pay_way, self.payment_intent_id)

    def test_marks_only_the_payment_failed(self):
        outcome = OrderService.handle_payment_failed(self.payment_intent_id)

        assert outcome.applied is True
        assert outcome.previous_payment_status == PaymentStatus.PENDING
        result = outcome.order
        assert result.id == self.order.id
        result.refresh_from_db()
        assert result.payment_status == PaymentStatus.FAILED
        assert result.status == OrderStatus.PENDING
        assert result.shipping_price == _eur("5.00")

    def test_unknown_payment_id_returns_none(self):
        assert OrderService.handle_payment_failed("pi_nonexistent") is None

    def test_a_repeated_event_is_idempotent(self):
        OrderService.handle_payment_failed(self.payment_intent_id)

        outcome = OrderService.handle_payment_failed(self.payment_intent_id)

        assert outcome.applied is False
        assert outcome.previous_payment_status == PaymentStatus.FAILED
        assert outcome.order.id == self.order.id
        self.order.refresh_from_db()
        assert self.order.payment_status == PaymentStatus.FAILED

    @pytest.mark.parametrize(
        "settled_payment_status",
        [
            PaymentStatus.COMPLETED,
            PaymentStatus.REFUNDED,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.CANCELED,
        ],
    )
    def test_does_not_regress_a_settled_payment_status(
        self, settled_payment_status
    ):
        """A delayed 'payment_failed' can arrive after 'charge.refunded'
        already moved the order to REFUNDED."""
        order = _pending_stripe_order(
            self.pay_way,
            "pi_settled_fail",
            payment_status=settled_payment_status,
            status=OrderStatus.DELIVERED,
            paid_amount=_eur("55.00"),
        )

        outcome = OrderService.handle_payment_failed("pi_settled_fail")

        assert outcome.applied is False
        assert outcome.previous_payment_status == settled_payment_status
        assert outcome.order.id == order.id
        order.refresh_from_db()
        assert order.payment_status == settled_payment_status


@pytest.mark.django_db
class TestValidateCartForCheckout:
    def setup_method(self):
        self.cart = Cart.objects.create(
            user=UserAccountFactory.create(num_addresses=0)
        )

    def test_empty_cart_is_invalid(self):
        result = OrderService.validate_cart_for_checkout(self.cart)

        assert result["valid"] is False
        assert result["insufficient_stock"] is False
        assert [str(e) for e in result["errors"]] == ["Cart is empty"]

    def test_cart_with_available_products_is_valid(self):
        product = ProductFactory.create(stock=20, active=True)
        CartItem.objects.create(cart=self.cart, product=product, quantity=2)

        result = OrderService.validate_cart_for_checkout(self.cart)

        assert result == {
            "valid": True,
            "errors": [],
            "warnings": [],
            "insufficient_stock": False,
        }

    def test_quantity_above_stock_is_an_insufficient_stock_error(self):
        product = ProductFactory.create(stock=5, active=True)
        CartItem.objects.create(cart=self.cart, product=product, quantity=10)

        result = OrderService.validate_cart_for_checkout(self.cart)

        assert result["valid"] is False
        assert result["insufficient_stock"] is True
        assert len(result["errors"]) == 1
        assert "Available: 5, Requested: 10" in str(result["errors"][0])
