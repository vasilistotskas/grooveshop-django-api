"""Earned points come back when an order's STATUS becomes REFUNDED.

The refund paths move ``payment_status`` and send ``order_refunded``;
``RETURNED`` -> ``REFUNDED`` is a separate manual step that used to send
nothing, so the points stayed. The reversal must also happen exactly once
when both a refund path and the status move touch the same order.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from loyalty.services import LoyaltyService
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.services import OrderService
from order.signals import order_refunded
from product.factories import ProductFactory
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


def _enabled(key, default=None):
    return {"LOYALTY_ENABLED": True}.get(key, default)


@pytest.fixture
def awarded_order():
    user = UserAccountFactory(num_addresses=0)
    order = OrderFactory(
        user=user,
        status=OrderStatus.RETURNED,
        payment_status=PaymentStatus.COMPLETED,
        num_order_items=0,
    )
    OrderItemFactory(
        order=order,
        product=ProductFactory(
            price=Money(Decimal("100.00"), "EUR"),
            discount_percent=Decimal(0),
            vat=None,
            stock=10,
            active=True,
        ),
        quantity=1,
        price=Money(Decimal("100.00"), "EUR"),
    )
    with patch("loyalty.services.Setting.get", side_effect=_enabled):
        earned = LoyaltyService.award_order_points(order.id)
    assert earned > 0
    return order, earned


def _other_points(user, amount=500):
    """Points from an unrelated order, so a second reversal of the
    refunded one would have a balance to eat into."""
    PointsTransaction.objects.create(
        user=user, points=amount, transaction_type=TransactionType.EARN
    )
    return amount


def _balance(user):
    return LoyaltyService.get_user_balance(user)


def _reversals(order):
    return PointsTransaction.objects.filter(
        reference_order=order, transaction_type=TransactionType.ADJUST
    )


def test_status_only_refund_reverses_the_points(
    awarded_order, django_capture_on_commit_callbacks
):
    order, earned = awarded_order
    assert _balance(order.user) == earned

    with (
        patch("loyalty.services.Setting.get", side_effect=_enabled),
        django_capture_on_commit_callbacks(execute=True),
    ):
        OrderService.update_order_status(order, OrderStatus.REFUNDED)

    assert _reversals(order).count() == 1
    assert _balance(order.user) == 0


def test_returned_alone_keeps_the_points(
    awarded_order, django_capture_on_commit_callbacks
):
    order, earned = awarded_order
    order.status = OrderStatus.SHIPPED
    order.save(update_fields=["status"])

    with (
        patch("loyalty.services.Setting.get", side_effect=_enabled),
        django_capture_on_commit_callbacks(execute=True),
    ):
        OrderService.update_order_status(order, OrderStatus.RETURNED)

    assert not _reversals(order).exists()
    assert _balance(order.user) == earned


def test_refund_then_status_move_reverses_exactly_once(
    awarded_order, django_capture_on_commit_callbacks
):
    order, _earned = awarded_order
    other = _other_points(order.user)

    with (
        patch("loyalty.services.Setting.get", side_effect=_enabled),
        django_capture_on_commit_callbacks(execute=True),
    ):
        # What OrderService.refund_order does once the provider agrees.
        order.payment_status = PaymentStatus.REFUNDED
        order.save(update_fields=["payment_status"])
        order_refunded.send(sender=OrderService, order=order)
    assert _reversals(order).count() == 1

    with (
        patch("loyalty.services.Setting.get", side_effect=_enabled),
        django_capture_on_commit_callbacks(execute=True),
    ):
        OrderService.update_order_status(order, OrderStatus.REFUNDED)

    assert _reversals(order).count() == 1
    assert _balance(order.user) == other
