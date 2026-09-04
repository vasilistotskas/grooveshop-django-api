"""Wholesale orders don't accrue loyalty points unless the merchant
opts in — the points basis is the RETAIL product price, so a
negotiated-price order would otherwise earn full retail-basis points
(a retail program silently subsidizing wholesale)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from loyalty.models import PointsTransaction
from loyalty.services import LoyaltyService
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.factories import ProductFactory
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db

_B2B_MARKER = {
    "group_id": 1,
    "group_name": "Wholesale",
    "discount_percent": "10.00",
}


def _completed_order(user, *, metadata=None):
    order = OrderFactory(user=user, metadata=metadata or {})
    product = ProductFactory(
        price=Money(Decimal("100.00"), "EUR"),
        discount_percent=Decimal(0),
        vat=None,
        stock=10,
        active=True,
    )
    OrderItemFactory(
        order=order,
        product=product,
        quantity=1,
        price=Money(Decimal("100.00"), "EUR"),
    )
    return order


def _loyalty_settings(extra=None):
    values = {"LOYALTY_ENABLED": True, **(extra or {})}

    def _get(key, default=None):
        return values.get(key, default)

    return patch("loyalty.services.Setting.get", side_effect=_get)


class TestB2BAccrualGate:
    def test_wholesale_order_earns_no_points_by_default(self):
        user = UserAccountFactory()
        order = _completed_order(user, metadata={"b2b_pricing": _B2B_MARKER})

        with _loyalty_settings():
            awarded = LoyaltyService.award_order_points(order.id)

        assert awarded == 0
        assert not PointsTransaction.objects.filter(user=user).exists()

    def test_merchant_opt_in_restores_accrual(self):
        user = UserAccountFactory()
        order = _completed_order(user, metadata={"b2b_pricing": _B2B_MARKER})

        with _loyalty_settings({"B2B_LOYALTY_ENABLED": True}):
            awarded = LoyaltyService.award_order_points(order.id)

        assert awarded > 0

    def test_retail_order_unaffected(self):
        user = UserAccountFactory()
        order = _completed_order(user)

        with _loyalty_settings():
            awarded = LoyaltyService.award_order_points(order.id)

        assert awarded > 0
