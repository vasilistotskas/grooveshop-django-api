"""What a poll of the payment provider may record on an order.

``GET /api/v1/order/{id}/payment_status`` (``OrderService.get_payment_status``)
asks the provider where an order's payment stands and records the answer.
Two answers were recorded wrongly:

- A provider that could not be asked (#698). A timeout, a connection error, a
  rate limit or an outage says nothing about the payment, but it was stored
  as FAILED, and ``auto_cancel_stuck_pending_orders`` cancels a PENDING order
  30 minutes after its payment FAILED: a shopper still paying could lose the
  order. Now nothing is written and the caller hears "unknown".
- A canceled attempt (#697). A provider that reports the payment canceled
  (a Stripe intent closed before a hosted session replaced it, a Viva
  ``X``) was stored as CANCELED, the payment state of a CLOSED order.
  On a PENDING order that state is settled, so neither sweep bucket selected
  it and its stock stayed reserved for good. The attempt is over, not the
  order: it is recorded as FAILED, which keeps the order payable (a new
  hosted session may be opened) and lets the sweep release its stock through
  ``cancel_order``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
import stripe
from django.urls import reverse
from django.utils import timezone
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APIClient

from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentStatusUnavailableError
from order.factories.order import OrderFactory
from order.inventory.stock import StockManager
from order.models.order import Order
from order.models.payment_transaction import PaymentTransaction
from order.payments.payment import (
    StripePaymentProvider,
    VivaWalletPaymentProvider,
)
from order.payments.payment_ledger import PaymentLedger
from order.services import OrderService
from order.tasks import auto_cancel_stuck_pending_orders
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from tests.utils.viva import FakeVivaApi
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

PAYMENT_ID = "pi_test_poll"
VIVA_TRANSACTION_ID = "b1a3067c-321b-4ec6-bc9d-1778aef2a19d"

PROVIDER_ERRORS = [
    stripe.APIConnectionError("Network error communicating with Stripe"),
    stripe.RateLimitError("Too many requests"),
    stripe.APIError("Stripe is having a bad day"),
]


def _stripe_credentials():
    return patch(
        "tenant.credentials.stripe_credentials",
        return_value={"secret_key": "sk_test_dummy"},
    )


def _order(
    provider_code="stripe",
    payment_id=PAYMENT_ID,
    payment_status=PaymentStatus.PENDING,
    **kwargs,
):
    order = OrderFactory(
        num_order_items=0,
        status=OrderStatus.PENDING,
        pay_way=PayWayFactory(
            provider_code=provider_code, settlement=PaySettlement.ONLINE
        ),
        **kwargs,
    )
    order.payment_status = payment_status
    order.payment_id = payment_id
    order.save(update_fields=["payment_status", "payment_id"])
    return order


def _provider_says(polled):
    provider = Mock()
    provider.get_payment_status.return_value = (
        polled,
        {"payment_id": PAYMENT_ID, "provider": "stripe"},
    )
    return patch(
        "order.payments.payment.get_payment_provider", return_value=provider
    )


class TestAProviderThatCannotBeAskedIsNotAnOutcome:
    @pytest.mark.parametrize(
        "error", PROVIDER_ERRORS, ids=lambda e: type(e).__name__
    )
    def test_stripe_raises_instead_of_answering_failed(self, error, caplog):
        caplog.set_level(logging.ERROR)
        with (
            _stripe_credentials(),
            patch.object(stripe.PaymentIntent, "retrieve", side_effect=error),
            pytest.raises(PaymentStatusUnavailableError) as raised,
        ):
            StripePaymentProvider().get_payment_status(PAYMENT_ID)

        assert raised.value.payment_id == PAYMENT_ID
        assert raised.value.__cause__ is error
        # The cause is logged with its traceback where it happened.
        assert any(
            r.levelname == "ERROR" and r.exc_info for r in caplog.records
        )

    @pytest.mark.parametrize("http_status", [429, 500, 503])
    def test_viva_raises_instead_of_answering_failed(self, http_status):
        with (
            FakeVivaApi(retrieve=(http_status, "Service Unavailable")),
            pytest.raises(PaymentStatusUnavailableError) as raised,
        ):
            VivaWalletPaymentProvider().get_payment_status(VIVA_TRANSACTION_ID)

        assert raised.value.payment_id == VIVA_TRANSACTION_ID

    @pytest.mark.parametrize(
        "error", PROVIDER_ERRORS, ids=lambda e: type(e).__name__
    )
    def test_a_stripe_error_leaves_the_pending_payment_pending(self, error):
        order = _order()

        with (
            _stripe_credentials(),
            patch.object(stripe.PaymentIntent, "retrieve", side_effect=error),
            pytest.raises(PaymentStatusUnavailableError),
        ):
            OrderService.get_payment_status(order)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_a_viva_server_error_leaves_the_pending_payment_pending(self):
        order = _order(
            provider_code="viva_wallet", payment_id=VIVA_TRANSACTION_ID
        )

        with (
            FakeVivaApi(retrieve=(503, "Service Unavailable")),
            pytest.raises(PaymentStatusUnavailableError),
        ):
            OrderService.get_payment_status(order)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_the_sweep_leaves_the_order_alone_after_a_failed_poll(self):
        """The harm the issue names: a poll that could not reach the
        provider, followed by the 30-minute sweep, canceled an order whose
        payment was fine."""
        order = _order()
        with (
            _stripe_credentials(),
            patch.object(
                stripe.PaymentIntent,
                "retrieve",
                side_effect=stripe.APIConnectionError("timeout"),
            ),
            pytest.raises(PaymentStatusUnavailableError),
        ):
            OrderService.get_payment_status(order)
        Order.objects.filter(pk=order.pk).update(
            updated_at=timezone.now() - timedelta(minutes=31)
        )

        result = auto_cancel_stuck_pending_orders()

        assert result["canceled_failed"] == 0
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_the_endpoint_answers_503_and_writes_nothing(self):
        user = UserAccountFactory(num_addresses=0)
        order = _order(user=user)
        client = APIClient()
        client.force_authenticate(user=user)

        with (
            _stripe_credentials(),
            patch.object(
                stripe.PaymentIntent,
                "retrieve",
                side_effect=stripe.APIConnectionError("timeout"),
            ),
        ):
            response = client.get(
                reverse("order-payment-status", args=[order.pk])
            )

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.data["code"] == "payment_provider_unavailable"
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_the_ledger_records_no_charge_it_could_not_read(self):
        order = _order(payment_status=PaymentStatus.COMPLETED)
        provider = Mock()
        provider.get_payment_status.side_effect = PaymentStatusUnavailableError(
            PAYMENT_ID
        )

        with patch(
            "order.payments.payment.get_payment_provider", return_value=provider
        ):
            charge = PaymentLedger.ensure_charge(
                order, provider_code="stripe", payment_id=PAYMENT_ID
            )

        assert charge is None
        assert not PaymentTransaction.objects.filter(order=order).exists()


class TestACanceledAttemptDoesNotCloseTheOrder:
    def test_a_polled_cancel_is_recorded_as_a_failed_attempt(self):
        order = _order()

        with _provider_says(PaymentStatus.CANCELED):
            reported, _data = OrderService.get_payment_status(order)

        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING
        assert order.payment_status == PaymentStatus.FAILED
        # The caller still hears what the provider said.
        assert reported == PaymentStatus.CANCELED

    def test_the_sweep_releases_the_stock_of_a_canceled_attempt(self):
        product = ProductFactory(stock=100, num_images=0, num_reviews=0)
        order = _order()
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=10,
            order_id=order.id,
            reason="test_setup: simulate checkout decrement",
        )
        order.items.create(
            product=product, price=Money(Decimal("10.00"), "EUR"), quantity=10
        )
        with _provider_says(PaymentStatus.CANCELED):
            OrderService.get_payment_status(order)
        Order.objects.filter(pk=order.pk).update(
            updated_at=timezone.now() - timedelta(minutes=31)
        )
        provider_class = Mock()
        provider_class.return_value.close_payment.return_value = True

        with patch(
            "order.tasks.get_payment_provider_class",
            return_value=provider_class,
        ):
            result = auto_cancel_stuck_pending_orders()

        assert result["canceled_failed"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        product.refresh_from_db()
        assert product.stock == 100

    @pytest.mark.parametrize(
        "polled", [PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED]
    )
    def test_a_polled_refund_is_left_to_the_ledger(self, polled):
        """A refunded state is derived from the refunds the ledger booked,
        never written by a poll onto a payment the order never settled."""
        order = _order()

        with _provider_says(polled):
            OrderService.get_payment_status(order)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING
