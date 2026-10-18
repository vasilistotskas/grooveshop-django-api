"""What a refund does to points the customer already spent.

``reverse_order_points`` takes back the points an order earned. When the
customer has spent them elsewhere the balance cannot cover it. With
``LOYALTY_REFUND_ALLOWS_NEGATIVE_BALANCE`` on (the default) the whole
earning comes off and the balance goes negative, so future earnings pay it
off and a negative balance blocks redeeming; off, the reversal stops at
zero and the spent points survive the refund (the earn-spend-refund loop).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from loyalty.services import LoyaltyService
from order.models.order import Order
from tests.utils.loyalty import loyalty_settings
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

SETTING = "LOYALTY_REFUND_ALLOWS_NEGATIVE_BALANCE"


def _earned_then_spent(earned=100, spent=80):
    """100 points earned on an order, 80 spent on something else: balance
    20, and a refund of the order wants 100 back."""
    user = UserAccountFactory(num_addresses=0)
    order = Order.objects.create(user=user)
    PointsTransaction.objects.create(
        user=user,
        points=earned,
        transaction_type=TransactionType.EARN,
        reference_order=order,
    )
    PointsTransaction.objects.create(
        user=user, points=-spent, transaction_type=TransactionType.REDEEM
    )
    user.total_xp = earned
    user.save(update_fields=["total_xp"])
    return user, order


class TestWhenTheBalanceMayGoNegative:
    def test_the_whole_earning_comes_off(self):
        user, order = _earned_then_spent()

        with loyalty_settings(**{SETTING: True}):
            assert LoyaltyService.reverse_order_points(order.id) == 100

        assert LoyaltyService.get_user_balance(user) == -80
        user.refresh_from_db()
        assert user.total_xp == 0

    def test_it_is_the_default(self):
        """No row for the setting: a store that never chose is protected."""
        user, order = _earned_then_spent()

        assert LoyaltyService.reverse_order_points(order.id) == 100

        assert LoyaltyService.get_user_balance(user) == -80

    def test_the_bonus_granted_for_the_order_comes_off_in_full_too(self):
        user, order = _earned_then_spent()
        PointsTransaction.objects.create(
            user=user,
            points=40,
            transaction_type=TransactionType.BONUS,
            reference_order=order,
        )

        with loyalty_settings(**{SETTING: True}):
            assert LoyaltyService.reverse_order_points(order.id) == 140

        # 100 earned + 40 bonus - 80 spent = 60, less the 140 taken back.
        assert LoyaltyService.get_user_balance(user) == -80

    def test_later_earnings_pay_the_debt_off(self):
        user, order = _earned_then_spent()
        with loyalty_settings(**{SETTING: True}):
            LoyaltyService.reverse_order_points(order.id)
        assert LoyaltyService.get_user_balance(user) == -80

        PointsTransaction.objects.create(
            user=user, points=90, transaction_type=TransactionType.EARN
        )

        assert LoyaltyService.get_user_balance(user) == 10

    def test_a_negative_balance_blocks_redeeming(self):
        user, order = _earned_then_spent()
        with loyalty_settings(**{SETTING: True}):
            LoyaltyService.reverse_order_points(order.id)
        assert LoyaltyService.get_user_balance(user) == -80

        with (
            loyalty_settings(LOYALTY_REDEMPTION_RATIO_EUR=100.0),
            pytest.raises(ValidationError, match="Insufficient points balance"),
        ):
            LoyaltyService.redeem_points(
                user, 1, "EUR", max_discount=Decimal(99999)
            )

        assert not PointsTransaction.objects.filter(
            user=user,
            transaction_type=TransactionType.REDEEM,
            points=-1,
        ).exists()

    def test_redeeming_works_again_once_the_balance_is_positive(self):
        user, order = _earned_then_spent()
        with loyalty_settings(**{SETTING: True}):
            LoyaltyService.reverse_order_points(order.id)
        PointsTransaction.objects.create(
            user=user, points=90, transaction_type=TransactionType.EARN
        )

        with loyalty_settings(LOYALTY_REDEMPTION_RATIO_EUR=100.0):
            discount = LoyaltyService.redeem_points(
                user, 10, "EUR", max_discount=Decimal(99999)
            )

        assert discount == Decimal("0.10")
        assert LoyaltyService.get_user_balance(user) == 0


class TestWhenTheBalanceIsClamped:
    def test_the_reversal_stops_at_zero(self):
        user, order = _earned_then_spent()

        with loyalty_settings(**{SETTING: False}):
            assert LoyaltyService.reverse_order_points(order.id) == 20

        assert LoyaltyService.get_user_balance(user) == 0

    def test_the_bonus_is_clamped_too(self):
        user, order = _earned_then_spent()
        PointsTransaction.objects.create(
            user=user,
            points=40,
            transaction_type=TransactionType.BONUS,
            reference_order=order,
        )

        with loyalty_settings(**{SETTING: False}):
            assert LoyaltyService.reverse_order_points(order.id) == 60

        assert LoyaltyService.get_user_balance(user) == 0
