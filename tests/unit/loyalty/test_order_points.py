"""Points an order earns, and points that expire, end to end in the DB.

Every expected figure is worked out by hand from the formula
``floor(price * factor * coefficient * quantity [* tier multiplier])
+ fixed points * quantity`` (``LoyaltyService.calculate_item_points``),
so the tests do not recompute it with the code under test.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from djmoney.money import Money

from loyalty.enum import TransactionType
from loyalty.models.tier import LoyaltyTier
from loyalty.models.transaction import PointsTransaction
from loyalty.services import LoyaltyService
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.models.order import Order
from product.factories import ProductFactory
from tests.utils.loyalty import loyalty_settings
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _completed_order(user):
    """Points are awarded on completion; a random factory state could be
    a canceled or refunded order, which earns nothing."""
    return OrderFactory(
        user=user,
        num_order_items=0,
        status=OrderStatus.COMPLETED,
        payment_status=PaymentStatus.COMPLETED,
    )


def _line(order, *, price, quantity, coefficient="1.00", fixed=0):
    product = ProductFactory(
        price=Money(Decimal(price), "EUR"),
        discount_percent=Decimal(0),
        vat=None,
        points_coefficient=Decimal(coefficient),
        points=fixed,
        num_images=0,
        num_reviews=0,
    )
    OrderItemFactory(
        order=order, product=product, quantity=quantity, price=product.price
    )


def _earned(order) -> list[int]:
    return sorted(
        PointsTransaction.objects.filter(
            reference_order=order, transaction_type=TransactionType.EARN
        ).values_list("points", flat=True)
    )


class TestAward:
    def test_each_line_earns_its_own_transaction_and_the_xp_is_their_sum(
        self,
    ):
        user = UserAccountFactory(num_addresses=0)
        order = _completed_order(user)
        _line(order, price="10.00", quantity=3)  # floor(10*2*3) = 60
        _line(order, price="19.99", quantity=2, coefficient="0.50", fixed=5)
        # floor(19.99*2*0.5*2) = floor(39.98) = 39, plus 5 * 2 fixed = 49
        _line(order, price="0.99", quantity=1)  # floor(1.98) = 1

        with loyalty_settings(LOYALTY_POINTS_FACTOR=2):
            awarded = LoyaltyService.award_order_points(order.id)

        assert _earned(order) == [1, 49, 60]
        assert awarded == 110
        user.refresh_from_db()
        assert user.total_xp == 110

    def test_a_guest_order_earns_nothing(self):
        order = OrderFactory(user=None, num_order_items=0)
        _line(order, price="10.00", quantity=1)

        with loyalty_settings():
            awarded = LoyaltyService.award_order_points(order.id)

        assert awarded == 0
        assert not PointsTransaction.objects.exists()

    @pytest.mark.parametrize(
        ("multiplier_enabled", "multiplier", "expected"),
        [
            # floor(10 * 1.5) + 3: the fixed points are never multiplied.
            (True, "1.50", 18),
            (True, "1.00", 13),
            (False, "1.50", 13),
        ],
    )
    def test_the_tier_multiplier_scales_only_the_calculated_points(
        self, multiplier_enabled, multiplier, expected
    ):
        tier = LoyaltyTier.objects.create(
            required_level=1, points_multiplier=Decimal(multiplier)
        )
        user = UserAccountFactory(num_addresses=0, loyalty_tier=tier)
        order = _completed_order(user)
        _line(order, price="10.00", quantity=1, fixed=3)

        with loyalty_settings(
            LOYALTY_TIER_MULTIPLIER_ENABLED=multiplier_enabled
        ):
            LoyaltyService.award_order_points(order.id)

        assert _earned(order) == [expected]


class TestExpiration:
    @staticmethod
    def _earn(user, points, *, days_ago):
        earn = PointsTransaction.objects.create(
            user=user, points=points, transaction_type=TransactionType.EARN
        )
        PointsTransaction.objects.filter(pk=earn.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
        return earn

    @staticmethod
    def _expired(user) -> list[int]:
        return sorted(
            PointsTransaction.objects.filter(
                user=user, transaction_type=TransactionType.EXPIRE
            ).values_list("points", flat=True)
        )

    def test_only_earnings_older_than_the_window_expire_once(self):
        first, second = UserAccountFactory.create_batch(2, num_addresses=0)
        self._earn(first, 40, days_ago=31)
        self._earn(first, 25, days_ago=29)
        self._earn(second, 70, days_ago=45)

        with loyalty_settings(LOYALTY_POINTS_EXPIRATION_DAYS=30):
            assert LoyaltyService.process_expiration() == 2
            # An earning already expired is never considered again.
            assert LoyaltyService.process_expiration() == 0

        assert self._expired(first) == [-40]
        assert self._expired(second) == [-70]
        assert LoyaltyService.get_user_balance(first) == 25

    def test_a_zero_day_window_turns_expiration_off(self):
        user = UserAccountFactory(num_addresses=0)
        self._earn(user, 40, days_ago=400)

        with loyalty_settings(LOYALTY_POINTS_EXPIRATION_DAYS=0):
            assert LoyaltyService.process_expiration() == 0

        assert self._expired(user) == []


class TestNewCustomerBonusReversal:
    """A canceled or refunded first order takes its bonus with it."""

    BONUS = {
        "LOYALTY_POINTS_FACTOR": 2,
        "LOYALTY_NEW_CUSTOMER_BONUS_ENABLED": True,
        "LOYALTY_NEW_CUSTOMER_BONUS_POINTS": 100,
    }

    def _earn_with_bonus(self, user):
        order = _completed_order(user)
        _line(order, price="10.00", quantity=3)  # floor(10*2*3) = 60
        with loyalty_settings(**self.BONUS):
            assert LoyaltyService.award_order_points(order.id) == 60
            assert LoyaltyService.check_new_customer_bonus(user, order) == 100
        return order

    @staticmethod
    def _adjustments(order) -> list[int]:
        return sorted(
            PointsTransaction.objects.filter(
                reference_order=order,
                transaction_type=TransactionType.ADJUST,
            ).values_list("points", flat=True)
        )

    def test_reversal_negates_the_earn_and_the_bonus_once(self):
        user = UserAccountFactory(num_addresses=0)
        order = self._earn_with_bonus(user)
        assert LoyaltyService.get_user_balance(user) == 160

        with loyalty_settings(**self.BONUS):
            assert LoyaltyService.reverse_order_points(order.id) == 160
            assert LoyaltyService.reverse_order_points(order.id) == 0

        assert self._adjustments(order) == [-100, -60]
        assert LoyaltyService.get_user_balance(user) == 0
        user.refresh_from_db()
        # The bonus never added XP, so only the earning comes off it.
        assert user.total_xp == 0

    def test_a_bonus_granted_for_another_order_is_untouched(self):
        user = UserAccountFactory(num_addresses=0)
        first = self._earn_with_bonus(user)
        second = _completed_order(user)
        _line(second, price="10.00", quantity=1)  # floor(10*2*1) = 20
        with loyalty_settings(**self.BONUS):
            assert LoyaltyService.award_order_points(second.id) == 20
            assert LoyaltyService.check_new_customer_bonus(user, second) == 0
            assert LoyaltyService.reverse_order_points(second.id) == 20

        assert self._adjustments(second) == [-20]
        assert self._adjustments(first) == []
        assert (
            PointsTransaction.objects.filter(
                reference_order=first, transaction_type=TransactionType.BONUS
            )
            .values_list("points", flat=True)
            .get()
            == 100
        )
        assert LoyaltyService.get_user_balance(user) == 160
        user.refresh_from_db()
        assert user.total_xp == 60

    def test_bonus_reversal_is_clamped_to_the_balance_left(self):
        user = UserAccountFactory(num_addresses=0)
        order = self._earn_with_bonus(user)
        PointsTransaction.objects.create(
            user=user, points=-120, transaction_type=TransactionType.REDEEM
        )

        with loyalty_settings(**self.BONUS):
            assert LoyaltyService.reverse_order_points(order.id) == 40

        assert self._adjustments(order) == [-40]
        assert LoyaltyService.get_user_balance(user) == 0
        user.refresh_from_db()
        assert user.total_xp == 20


REVERSED = [
    pytest.param({"status": OrderStatus.CANCELED}, id="canceled"),
    pytest.param({"payment_status": PaymentStatus.REFUNDED}, id="refunded"),
    pytest.param(
        {"payment_status": PaymentStatus.PARTIALLY_REFUNDED},
        id="partially-refunded",
    ),
]


class TestNothingIsGrantedOnceAReversalIsDue:
    """Cancelling or refunding an order queues ``reverse_order_points``.
    Points granted after that ran would never be taken back, so the award
    and the bonus refuse an order in that state, whichever task runs
    first."""

    BONUS = TestNewCustomerBonusReversal.BONUS

    @pytest.mark.parametrize("reversed_state", REVERSED)
    def test_an_award_arriving_after_the_reversal_grants_nothing(
        self, reversed_state
    ):
        user = UserAccountFactory(num_addresses=0)
        order = _completed_order(user)
        _line(order, price="10.00", quantity=3)
        Order.objects.filter(pk=order.pk).update(**reversed_state)

        with loyalty_settings(**self.BONUS):
            assert LoyaltyService.award_order_points(order.id) == 0

        assert _earned(order) == []
        user.refresh_from_db()
        assert user.total_xp == 0

    @pytest.mark.parametrize("reversed_state", REVERSED)
    def test_a_reversal_between_the_award_and_the_bonus_blocks_the_bonus(
        self, reversed_state
    ):
        user = UserAccountFactory(num_addresses=0)
        order = _completed_order(user)
        _line(order, price="10.00", quantity=3)  # floor(10*2*3) = 60

        with loyalty_settings(**self.BONUS):
            assert LoyaltyService.award_order_points(order.id) == 60
            Order.objects.filter(pk=order.pk).update(**reversed_state)
            assert LoyaltyService.reverse_order_points(order.id) == 60
            assert LoyaltyService.check_new_customer_bonus(user, order) == 0

        assert not PointsTransaction.objects.filter(
            reference_order=order, transaction_type=TransactionType.BONUS
        ).exists()
        assert LoyaltyService.get_user_balance(user) == 0
