"""Refusal and failure branches of ``OrderService.refund_order`` and the
refund leg of ``OrderService.cancel_order`` (``order/services.py``).

The happy paths are covered in ``test_service_order.py`` and
``test_partial_refunds.py``; this module pins what happens when a refund
cannot or does not go through. Only the PSP
(``order.payment.get_payment_provider``) is mocked.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from djmoney.money import Money
from rest_framework.test import APIClient

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories import PaymentTransactionFactory
from order.factories.order import OrderFactory
from order.models.order import Order
from order.services import OrderService
from pay_way.factories import PayWayFactory
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _paid_order(**kwargs):
    """An order Stripe captured 50.00 for, as its ledger says."""
    kwargs.setdefault("pay_way", PayWayFactory.create_online_payment())
    kwargs.setdefault("status", OrderStatus.COMPLETED)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    kwargs.setdefault("payment_id", "pi_paid_123")
    kwargs.setdefault("amount_due", Money(Decimal("50.00"), "EUR"))
    order = OrderFactory(num_order_items=0, **kwargs)
    if order.payment_id:
        PaymentTransactionFactory(
            order=order,
            amount=Money(Decimal("50.00"), "EUR"),
            provider_reference=order.payment_id,
        )
    return order


def _status_in_db(order) -> str:
    return Order.objects.values_list("payment_status", flat=True).get(
        pk=order.pk
    )


def _refund_rows(order):
    return order.payment_transactions.filter(kind=PaymentTransactionKind.REFUND)


class TestRefundOrderRefusals:
    def test_amount_above_the_refundable_is_refused_before_the_provider(self):
        order = _paid_order()

        with patch("order.payment.get_payment_provider") as get_provider:
            with pytest.raises(PaymentError) as excinfo:
                OrderService.refund_order(
                    order, amount=Money(Decimal("50.01"), "EUR")
                )

        assert str(excinfo.value) == (
            "Refund amount (50.01) cannot exceed the refundable amount (50.00)."
        )
        get_provider.assert_not_called()
        assert _status_in_db(order) == PaymentStatus.COMPLETED

    def test_amount_equal_to_the_refundable_reaches_the_provider(self):
        order = _paid_order()
        amount = Money(Decimal("50.00"), "EUR")

        with patch("order.payment.get_payment_provider") as get_provider:
            provider = get_provider.return_value
            provider.refund_payment.return_value = (
                True,
                {"refund_id": "re_1", "amount": "50.00"},
            )
            result = OrderService.refund_order(order, amount=amount)

        assert result.complete is True
        assert result.refunded == amount
        get_provider.assert_called_once_with("stripe")
        provider.refund_payment.assert_called_once_with(
            payment_id="pi_paid_123",
            amount=amount,
            idempotency_key=f"order-{order.pk}-refund-1",
        )
        # The ledger says nothing is left, so the order reads REFUNDED.
        assert _status_in_db(order) == PaymentStatus.REFUNDED

    @pytest.mark.parametrize("amount", ["0.00", "-5.00"])
    def test_an_amount_that_is_not_positive_is_refused(self, amount):
        order = _paid_order()

        with patch("order.payment.get_payment_provider") as get_provider:
            with pytest.raises(PaymentError) as excinfo:
                OrderService.refund_order(
                    order, amount=Money(Decimal(amount), "EUR")
                )

        assert str(excinfo.value) == (
            "The refund amount must be greater than zero."
        )
        get_provider.assert_not_called()

    def test_an_order_the_ledger_holds_no_payment_for_is_refused(self):
        """COMPLETED by a courier, a gift card or a hand edit: no provider
        captured anything, so there is nothing to refund through one."""
        order = _paid_order(payment_id="")

        with patch("order.payment.get_payment_provider") as get_provider:
            with pytest.raises(PaymentError) as excinfo:
                OrderService.refund_order(order)

        assert str(excinfo.value) == (
            "This order was not paid through a payment provider, so there "
            "is nothing to refund here."
        )
        get_provider.assert_not_called()
        assert _status_in_db(order) == PaymentStatus.COMPLETED

    def test_an_unpaid_order_is_refused_without_asking_the_provider(self):
        """It names a payment intent (opened at checkout) and an online pay
        way, yet nothing was captured: there is nothing to look for."""
        order = _paid_order(payment_status=PaymentStatus.PENDING)
        order.payment_transactions.all().delete()

        with patch("order.payment.get_payment_provider") as get_provider:
            with pytest.raises(PaymentError) as excinfo:
                OrderService.refund_order(order)

        assert str(excinfo.value) == "This order has not been paid yet."
        get_provider.assert_not_called()

    def test_the_status_is_read_under_the_lock_not_from_the_callers_copy(self):
        order = _paid_order(payment_id="")
        stale = Order.objects.get(pk=order.pk)
        stale.payment_status = PaymentStatus.PENDING  # never saved

        with pytest.raises(PaymentError) as excinfo:
            OrderService.refund_order(stale)

        assert str(excinfo.value) == (
            "This order was not paid through a payment provider, so there "
            "is nothing to refund here."
        )

    def test_a_fully_refunded_order_is_refused(self):
        order = _paid_order()
        with patch("order.payment.get_payment_provider") as get_provider:
            get_provider.return_value.refund_payment.return_value = (
                True,
                {"refund_id": "re_1", "amount": "50.00"},
            )
            OrderService.refund_order(order)

            with pytest.raises(PaymentError) as excinfo:
                OrderService.refund_order(order)

        assert str(excinfo.value) == "This order has already been refunded."
        assert get_provider.return_value.refund_payment.call_count == 1

    def test_provider_decline_is_returned_and_nothing_is_recorded(self):
        order = _paid_order()

        with (
            patch("order.payment.get_payment_provider") as get_provider,
            patch("order.services.order_refunded.send") as refunded_signal,
        ):
            get_provider.return_value.refund_payment.return_value = (
                False,
                {"error": "charge_already_refunded"},
            )
            result = OrderService.refund_order(order, reason="customer")

        assert result.complete is False
        assert result.paid_something is False
        assert result.refund_ids == ()
        assert result.declined == {"error": "charge_already_refunded"}
        refunded_signal.assert_not_called()
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert not _refund_rows(order).exists()


def _cancellable_paid_order(**kwargs):
    kwargs.setdefault("status", OrderStatus.PENDING)
    return _paid_order(**kwargs)


class TestCancelOrderWhenTheRefundFails:
    def test_declined_refund_still_cancels_and_reports_the_failure(self):
        order = _cancellable_paid_order()

        with patch("order.payment.get_payment_provider") as get_provider:
            get_provider.return_value.refund_payment.return_value = (
                False,
                {"error": "stripe said: insufficient balance acct_123"},
            )
            canceled, refund_info = OrderService.cancel_order(
                order, reason="changed mind", refund_payment=True
            )

        assert refund_info == {
            "refunded": False,
            "error": "The refund could not be processed.",
            "message": "Order canceled but refund failed",
        }
        assert canceled.status == OrderStatus.CANCELED
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        # The money was taken and not returned: the payment stays
        # COMPLETED so the order still shows as owing a refund.
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.metadata["cancellation"]["reason"] == "changed mind"
        assert not _refund_rows(order).exists()

    def test_provider_exception_still_cancels_and_reports_the_failure(self):
        order = _cancellable_paid_order()

        with patch("order.payment.get_payment_provider") as get_provider:
            get_provider.return_value.refund_payment.side_effect = RuntimeError(
                "stripe timeout"
            )
            _canceled, refund_info = OrderService.cancel_order(
                order, reason="", refund_payment=True
            )

        assert refund_info == {
            "refunded": False,
            "error": "The refund could not be processed.",
            "message": "Order canceled but refund failed",
        }
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.COMPLETED
        assert not _refund_rows(order).exists()

    def test_a_refund_declined_part_way_says_part_of_it_was_made(self):
        """Paid twice, the second payment's refund declined: the first
        share moved, so the cancel must not claim nothing did."""
        order = _cancellable_paid_order()
        PaymentTransactionFactory(
            order=order,
            amount=Money(Decimal("20.00"), "EUR"),
            provider_reference="pi_second",
        )

        with patch("order.payment.get_payment_provider") as get_provider:
            get_provider.return_value.refund_payment.side_effect = [
                (True, {"refund_id": "re_1", "amount": "50.00"}),
                (False, {"error": "stripe said: declined acct_123"}),
            ]
            _canceled, refund_info = OrderService.cancel_order(
                order, reason="changed mind", refund_payment=True
            )

        assert refund_info == {
            "refunded": False,
            "error": "The refund could not be processed.",
            "message": "Order canceled but only part of the payment was refunded",
        }
        assert [r.provider_reference for r in _refund_rows(order)] == ["re_1"]
        assert _status_in_db(order) == PaymentStatus.PARTIALLY_REFUNDED

    def test_refund_payment_false_never_calls_the_provider(self):
        order = _cancellable_paid_order()

        with patch("order.payment.get_payment_provider") as get_provider:
            _canceled, refund_info = OrderService.cancel_order(
                order, refund_payment=False
            )

        assert refund_info is None
        get_provider.assert_not_called()
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.COMPLETED

    def test_cancel_endpoint_surfaces_the_failed_refund_without_provider_text(
        self,
    ):
        user = UserAccountFactory()
        order = _cancellable_paid_order(user=user)
        client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
        client.force_authenticate(user=user)

        with patch("order.payment.get_payment_provider") as get_provider:
            get_provider.return_value.refund_payment.return_value = (
                False,
                {"error": "stripe said: insufficient balance acct_123"},
            )
            response = client.post(
                reverse("order-cancel", kwargs={"pk": order.pk}),
                {"reason": "changed mind"},
                format="json",
            )

        assert response.status_code == 200, response.data
        assert response.data["status"] == OrderStatus.CANCELED
        assert response.data["refund_info"] == {
            "refunded": False,
            "error": "The refund could not be processed.",
            "message": "Order canceled but refund failed",
        }
        assert "acct_123" not in str(response.data)
