"""``LoyaltyService.get_order_points`` — the points an order earns.

The storefront shows it on the order page, so it has to agree with what
``award_order_points`` then writes, and go quiet in every case the award
refuses.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from loyalty.models import PointsTransaction
from loyalty.services import LoyaltyService
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.factories import ProductFactory
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


def _order(user, *, metadata=None, status=OrderStatus.COMPLETED):
    order = OrderFactory(
        user=user,
        metadata=metadata or {},
        status=status,
        payment_status=PaymentStatus.COMPLETED,
        num_order_items=0,
    )
    for price in ("100.00", "40.00"):
        OrderItemFactory(
            order=order,
            product=ProductFactory(
                price=Money(Decimal(price), "EUR"),
                discount_percent=Decimal(0),
                vat=None,
                stock=10,
                active=True,
            ),
            quantity=1,
            price=Money(Decimal(price), "EUR"),
        )
    return order


def _settings(extra=None, *, enabled=True):
    values = {"LOYALTY_ENABLED": enabled, **(extra or {})}
    return patch(
        "loyalty.services.Setting.get",
        side_effect=lambda key, default=None: values.get(key, default),
    )


def test_projection_equals_what_the_award_writes():
    order = _order(UserAccountFactory())

    with _settings():
        projected = LoyaltyService.get_order_points(order)
        awarded = LoyaltyService.award_order_points(order.id)

    assert projected == awarded > 0


def test_after_the_award_it_reports_the_recorded_points():
    order = _order(UserAccountFactory())
    with _settings():
        awarded = LoyaltyService.award_order_points(order.id)

    # A different factor now must not rewrite history.
    with _settings({"LOYALTY_POINTS_FACTOR": 5}):
        assert LoyaltyService.get_order_points(order) == awarded


def test_zero_when_the_program_is_off():
    order = _order(UserAccountFactory())

    with _settings(enabled=False):
        assert LoyaltyService.get_order_points(order) == 0


def test_zero_for_an_order_without_an_account():
    order = _order(UserAccountFactory())
    order.user = None

    with _settings():
        assert LoyaltyService.get_order_points(order) == 0


def test_zero_for_a_canceled_order():
    order = _order(UserAccountFactory(), status=OrderStatus.CANCELED)

    with _settings():
        assert LoyaltyService.get_order_points(order) == 0
    assert not PointsTransaction.objects.filter(reference_order=order).exists()


def test_zero_for_wholesale_unless_the_merchant_opted_in():
    order = _order(
        UserAccountFactory(), metadata={"b2b_pricing": {"group_id": 1}}
    )

    with _settings():
        assert LoyaltyService.get_order_points(order) == 0
    with _settings({"B2B_LOYALTY_ENABLED": True}):
        assert LoyaltyService.get_order_points(order) > 0


def test_zero_for_an_order_refunded_by_status_alone():
    """RETURNED -> REFUNDED changes only ``status``; the payment status
    can still read COMPLETED."""
    order = _order(UserAccountFactory(), status=OrderStatus.REFUNDED)
    assert order.payment_status == PaymentStatus.COMPLETED

    with _settings():
        assert LoyaltyService.get_order_points(order) == 0
        assert LoyaltyService.award_order_points(order.id) == 0
