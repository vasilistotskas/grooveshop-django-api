"""A settled payment state is final; polling the provider cannot undo it.

Providers report the state of the PAYMENT, which is not the state of the
money: a Stripe PaymentIntent stays ``succeeded`` forever after the
charge is refunded, and Viva's transaction lookup behaves the same way.
Every path that writes a polled status therefore has to refuse to move an
order out of COMPLETED / REFUNDED / PARTIALLY_REFUNDED / CANCELED — the
rule the webhook handlers already followed and the polling paths did not.

The customer-facing reach of this: ``GET /api/v1/order/{id}/payment_status``
is a read-only action on the order detail page, so a refunded order lost
its refund the next time its own owner opened the page.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from order.enum.status import SETTLED_PAYMENT_STATUSES, PaymentStatus
from order.factories.order import OrderFactory
from order.services import OrderService

pytestmark = pytest.mark.django_db


def _order(payment_status):
    order = OrderFactory(num_order_items=0)
    order.payment_status = payment_status
    order.payment_id = "pi_test_123"
    order.save(update_fields=["payment_status", "payment_id"])
    return order


def _provider_says(status):
    provider = Mock()
    provider.get_payment_status.return_value = (status, {"id": "pi_test_123"})
    return patch("order.payment.get_payment_provider", return_value=provider)


@pytest.mark.parametrize("settled", sorted(SETTLED_PAYMENT_STATUSES))
def test_polling_never_moves_a_settled_order(settled):
    order = _order(settled)

    with _provider_says(PaymentStatus.COMPLETED):
        reported, _data = OrderService.get_payment_status(order)

    order.refresh_from_db()
    assert order.payment_status == settled
    # The provider's answer is still reported to the caller — only the
    # write is refused.
    assert reported == PaymentStatus.COMPLETED


def test_refund_survives_the_order_page_poll():
    """The concrete regression: a refunded order polled by its owner."""
    order = _order(PaymentStatus.REFUNDED)

    with _provider_says(PaymentStatus.COMPLETED):
        OrderService.get_payment_status(order)

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.REFUNDED
    assert order.payment_status != PaymentStatus.COMPLETED


def test_polling_still_advances_an_unsettled_order():
    """The guard must not freeze the case polling exists for."""
    order = _order(PaymentStatus.PENDING)

    with _provider_says(PaymentStatus.COMPLETED):
        OrderService.get_payment_status(order)

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.COMPLETED


def test_update_order_false_writes_nothing():
    order = _order(PaymentStatus.PENDING)

    with _provider_says(PaymentStatus.COMPLETED):
        OrderService.get_payment_status(order, update_order=False)

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.PENDING


class TestPayWayServicePath:
    """``PayWayService.check_payment_status`` polls the same providers and
    additionally calls ``mark_as_paid``, so it needs the same guard."""

    def test_settled_order_is_not_reopened(self):
        from pay_way.services import PayWayService

        order = _order(PaymentStatus.REFUNDED)
        pay_way = Mock(is_online_payment=True)
        provider = Mock()
        provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {},
        )

        with patch.object(
            PayWayService, "get_provider_for_pay_way", return_value=provider
        ):
            reported, _data = PayWayService.check_payment_status(pay_way, order)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert reported == PaymentStatus.REFUNDED

    def test_pending_order_still_settles(self):
        from pay_way.services import PayWayService

        order = _order(PaymentStatus.PENDING)
        pay_way = Mock(is_online_payment=True)
        provider = Mock()
        provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {},
        )

        with patch.object(
            PayWayService, "get_provider_for_pay_way", return_value=provider
        ):
            PayWayService.check_payment_status(pay_way, order)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
