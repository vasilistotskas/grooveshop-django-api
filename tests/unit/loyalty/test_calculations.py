"""``LoyaltyService``'s arithmetic: price basis, points, level, tier,
balance and the redemption price.

Branching rules are pinned with hand-worked cases. The points formula
also gets one property, stated independently of the implementation:
``floor`` never rounds up and never loses a whole point, whatever the
float-to-Decimal path the configured factor takes.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError
from hypothesis import given
from hypothesis import strategies as st

from loyalty.enum import PriceBasis, TransactionType
from loyalty.models.tier import LoyaltyTier
from loyalty.models.transaction import PointsTransaction
from loyalty.services import LoyaltyService
from tests.utils.loyalty import loyalty_settings
from user.factories.account import UserAccountFactory


def _product(
    *,
    price="100.00",
    vat="24.00",
    discount="10.00",
    final="114.00",
    coefficient="1.00",
    fixed=0,
):
    product = MagicMock()
    product.price.amount = Decimal(price)
    product.vat_value.amount = Decimal(vat)
    product.discount_value.amount = Decimal(discount)
    product.final_price.amount = Decimal(final)
    product.points_coefficient = Decimal(coefficient)
    product.points = fixed
    return product


@pytest.mark.parametrize(
    ("basis", "expected"),
    [
        (PriceBasis.PRICE_EXCL_VAT_NO_DISCOUNT, Decimal("100.00")),
        (PriceBasis.PRICE_EXCL_VAT_WITH_DISCOUNT, Decimal("90.00")),
        (PriceBasis.PRICE_INCL_VAT_NO_DISCOUNT, Decimal("124.00")),
        (PriceBasis.FINAL_PRICE, Decimal("114.00")),
    ],
)
def test_price_basis_selects_its_price_component(basis, expected):
    with loyalty_settings(LOYALTY_PRICE_BASIS=basis):
        assert LoyaltyService.get_price_basis_amount(_product()) == expected


_cents = st.decimals(
    min_value=Decimal("0.01"), max_value=Decimal("99999.99"), places=2
)


@given(
    price=_cents,
    coefficient=st.decimals(
        min_value=Decimal("0.00"), max_value=Decimal("99.99"), places=2
    ),
    factor=_cents,
    fixed=st.integers(min_value=0, max_value=10_000),
    quantity=st.integers(min_value=1, max_value=100),
)
def test_item_points_floor_the_exact_product_and_add_fixed_points(
    price, coefficient, factor, fixed, quantity
):
    product = _product(
        price=price, final=price, coefficient=coefficient, fixed=fixed
    )
    # Settings hold floats; the service must not lose precision on them.
    with loyalty_settings(LOYALTY_POINTS_FACTOR=float(factor)):
        points = LoyaltyService.calculate_item_points(product, quantity)

    earned = points - fixed * quantity
    exact = price * factor * coefficient * quantity
    assert 0 <= exact - earned < 1


@pytest.mark.parametrize(
    ("total_xp", "level"), [(0, 1), (999, 1), (1000, 2), (2500, 3)]
)
def test_level_is_one_plus_each_full_step_of_xp(total_xp, level):
    with loyalty_settings(LOYALTY_XP_PER_LEVEL=1000):
        assert (
            LoyaltyService.get_user_level(MagicMock(total_xp=total_xp)) == level
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("level", "tier_level"), [(4, None), (5, 5), (20, 15), (99, 30)]
)
def test_the_tier_is_the_highest_one_the_level_reaches(level, tier_level):
    for required_level in (5, 15, 30):
        LoyaltyTier.objects.create(
            required_level=required_level, points_multiplier=Decimal("1.00")
        )

    tier = LoyaltyTier.objects.get_for_level(level)

    assert (tier and tier.required_level) == tier_level


@pytest.mark.django_db
def test_the_balance_is_the_signed_sum_of_every_transaction():
    user = UserAccountFactory(num_addresses=0)
    for points, kind in (
        (500, TransactionType.EARN),
        (50, TransactionType.BONUS),
        (-200, TransactionType.REDEEM),
        (-30, TransactionType.EXPIRE),
        (-20, TransactionType.ADJUST),
    ):
        PointsTransaction.objects.create(
            user=user, points=points, transaction_type=kind
        )

    assert LoyaltyService.get_user_balance(user) == 300


@pytest.mark.django_db
class TestRedemptionPrice:
    @pytest.fixture
    def member(self):
        user = UserAccountFactory(num_addresses=0)
        PointsTransaction.objects.create(
            user=user, points=1000, transaction_type=TransactionType.EARN
        )
        return user

    @pytest.mark.parametrize(
        ("ratio", "discount"),
        [(100.0, Decimal(5)), (3.0, Decimal(500) / Decimal(3))],
    )
    def test_points_buy_their_ratio_worth_unrounded(
        self, member, ratio, discount
    ):
        with loyalty_settings(LOYALTY_REDEMPTION_RATIO_EUR=ratio):
            price = LoyaltyService.preview_redemption(
                member, 500, "EUR", max_discount=Decimal(1000)
            )

        assert price == discount

    def test_a_preview_writes_nothing(self, member):
        with loyalty_settings(LOYALTY_REDEMPTION_RATIO_EUR=100.0):
            LoyaltyService.preview_redemption(
                member, 500, "EUR", max_discount=Decimal(1000)
            )

        assert LoyaltyService.get_user_balance(member) == 1000

    def test_the_price_may_not_exceed_the_cap(self, member):
        with (
            loyalty_settings(LOYALTY_REDEMPTION_RATIO_EUR=100.0),
            pytest.raises(ValidationError),
        ):
            LoyaltyService.preview_redemption(
                member, 500, "EUR", max_discount=Decimal("4.99")
            )
