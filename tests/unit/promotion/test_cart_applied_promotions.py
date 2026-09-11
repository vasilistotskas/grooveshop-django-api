"""``applied_promotions``: which offers took money off, and how much.

The cart used to expose one opaque ``promotion_discount`` number. A
shopper could not tell which offers applied, and the coupon row showed
that combined total as though the code had earned all of it — a real
staging cart read "SAVE5 −39,98 €" when SAVE5 was worth 5,00 € and two
automatic offers accounted for the other 34,98 €.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cart.serializers.cart import CartSerializer
from promotion.enum import BenefitType, PromotionTrigger
from promotion.factories import PromotionCodeFactory, PromotionFactory
from promotion.models import CartPromotionCode

pytestmark = pytest.mark.django_db


def _breakdown(cart):
    return CartSerializer(cart).data["applied_promotions"]


def test_an_automatic_promotion_is_named_with_its_own_amount(
    enable_promotions, make_cart
):
    cart, _ = make_cart([(100, 1)])
    promotion = PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FIXED_AMOUNT,
        benefit_value=Decimal(15),
        stackable=True,
    )

    (entry,) = _breakdown(cart)
    assert entry["promotionId"] == promotion.id
    assert entry["amount"] == Decimal("15.00")
    assert entry["code"] is None
    assert entry["name"]


def test_a_coupon_entry_carries_its_code(enable_promotions, make_cart):
    cart, _ = make_cart([(100, 1)])
    code = PromotionCodeFactory(
        promotion=PromotionFactory(
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(5),
            stackable=True,
        )
    )
    CartPromotionCode.objects.create(cart=cart, code=code)

    (entry,) = _breakdown(cart)
    assert entry["code"] == code.code
    assert entry["amount"] == Decimal("5.00")


def test_the_coupon_is_separable_from_the_automatic_offers(
    enable_promotions, make_cart
):
    """The reported bug, in the shape it was reported.

    Two automatic offers plus a 5 € coupon. The coupon row must be able
    to show 5 €, not the combined total.
    """
    cart, _ = make_cart([(100, 1)])
    PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FIXED_AMOUNT,
        benefit_value=Decimal(15),
        stackable=True,
    )
    PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FIXED_AMOUNT,
        benefit_value=Decimal(20),
        stackable=True,
    )
    code = PromotionCodeFactory(
        promotion=PromotionFactory(
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(5),
            stackable=True,
        )
    )
    CartPromotionCode.objects.create(cart=cart, code=code)

    data = CartSerializer(cart).data
    breakdown = data["applied_promotions"]

    assert len(breakdown) == 3
    by_code = {entry["code"]: entry["amount"] for entry in breakdown}
    assert by_code[code.code] == Decimal("5.00")
    assert sum(entry["amount"] for entry in breakdown) == Decimal(
        str(data["promotion_discount"])
    )


def test_the_amounts_always_sum_to_the_headline_discount(
    enable_promotions, make_cart
):
    cart, _ = make_cart([(80, 1), (20, 2)])
    PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_value=Decimal(10),
        stackable=True,
    )
    PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FIXED_AMOUNT,
        benefit_value=Decimal(7),
        stackable=True,
    )

    data = CartSerializer(cart).data

    assert sum(e["amount"] for e in data["applied_promotions"]) == Decimal(
        str(data["promotion_discount"])
    )


def test_a_coupon_that_won_nothing_contributes_no_entry(
    enable_promotions, make_cart
):
    """A code that loses the stacking comparison stays attached to the
    cart, so ``applied_coupon_codes`` still lists it. It must NOT show
    up in the breakdown, or the UI would again credit it with money it
    did not earn."""
    cart, _ = make_cart([(100, 1)])
    PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FIXED_AMOUNT,
        benefit_value=Decimal(30),
        stackable=False,
    )
    code = PromotionCodeFactory(
        promotion=PromotionFactory(
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(5),
            stackable=False,
        )
    )
    CartPromotionCode.objects.create(cart=cart, code=code)

    data = CartSerializer(cart).data

    assert code.code in data["applied_coupon_codes"]
    assert code.code not in {e["code"] for e in data["applied_promotions"]}


def test_no_promotions_means_an_empty_breakdown(enable_promotions, make_cart):
    cart, _ = make_cart([(100, 1)])

    assert _breakdown(cart) == []
