"""Refusal and failure branches of ``OrderService.refund_order`` and the
refund leg of ``OrderService.cancel_order`` (``order/services.py``).

The happy paths are covered in ``test_service_order.py``; this module
pins what happens when a refund cannot or does not go through. Only the
PSP (``order.payment.get_payment_provider``) is mocked.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from djmoney.money import Money
from rest_framework.test import APIClient

from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories.order import OrderFactory
from order.models.order import Order
from order.services import OrderService
from pay_way.factories import PayWayFactory
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _paid_order(**kwargs):
    kwargs.setdefault("pay_way", PayWayFactory.create_online_payment())
    kwargs.setdefault("status", OrderStatus.COMPLETED)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    kwargs.setdefault("payment_id", "pi_paid_123")
    kwargs.setdefault("paid_amount", Money(Decimal("50.00"), "EUR"))
    return OrderFactory(num_order_items=0, **kwargs)


def _status_in_db(order) -> str:
    return Order.objects.values_list("payment_status", flat=True).get(
        pk=order.pk
    )


class TestRefundOrderRefusals:
    def test_amount_above_paid_amount_is_refused_before_the_provider(self):
        order = _paid_order()

        with patch("order.payment.get_payment_provider") as get_provider:
            with pytest.raises(PaymentError) as excinfo:
                OrderService.refund_order(
                    order, amount=Money(Decimal("50.01"), "EUR")
                )

        assert str(excinfo.value) == (
            "Refund amount (50.01) cannot exceed paid amount (50.00)."
        )
        get_provider.assert_not_called()
        assert _status_in_db(order) == PaymentStatus.COMPLETED

    def test_amount_equal_to_paid_amount_reaches_the_provider(self):
        order = _paid_order()
        amount = Money(Decimal("50.00"), "EUR")

        with patch("order.payment.get_payment_provider") as get_provider:
            provider = get_provider.return_value
            provider.refund_payment.return_value = (
                True,
                {"refund_id": "re_1", "status": PaymentStatus.REFUNDED},
            )
            success, _response = OrderService.refund_order(order, amount=amount)

        assert success is True
        get_provider.assert_called_once_with("stripe")
        provider.refund_payment.assert_called_once_with(
            payment_id="pi_paid_123", amount=amount
        )
        assert _status_in_db(order) == PaymentStatus.REFUNDED

    def test_order_without_pay_way_is_refused(self):
        order = _paid_order()
        Order.objects.filter(pk=order.pk).update(pay_way=None)
        order.refresh_from_db()

        with patch("order.payment.get_payment_provider") as get_provider:
            with pytest.raises(PaymentError) as excinfo:
                OrderService.refund_order(order)

        assert str(excinfo.value) == (
            "This order has no payment method configured."
        )
        get_provider.assert_not_called()
        assert _status_in_db(order) == PaymentStatus.COMPLETED

    def test_order_without_payment_id_is_refused(self):
        order = _paid_order(payment_id="")

        with patch("order.payment.get_payment_provider") as get_provider:
            with pytest.raises(PaymentError) as excinfo:
                OrderService.refund_order(order)

        assert str(excinfo.value) == ("This order has no payment ID to refund.")
        get_provider.assert_not_called()

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

        assert result == (False, {"error": "charge_already_refunded"})
        refunded_signal.assert_not_called()
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert "refunds" not in (order.metadata or {})


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
        assert "refunds" not in order.metadata

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
