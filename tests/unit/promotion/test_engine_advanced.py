"""BXGY / FREE_GIFT / exclusions / min-quantity / near-miss semantics."""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from product.factories import ProductFactory
from promotion.enum import (
    BenefitType,
    CouponRejectionReason,
    PromotionTrigger,
    TargetScope,
)
from promotion.factories import PromotionCodeFactory, PromotionFactory
from promotion.models import CartPromotionCode
from promotion.services import PromotionEngine

pytestmark = pytest.mark.django_db


class TestExclusions:
    def test_excluded_products_shrink_the_base(
        self, enable_promotions, make_cart
    ):
        cart, products = make_cart([(100, 1), (50, 1)])
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(10),
        )
        promotion.excluded_products.add(products[0])

        result = PromotionEngine.evaluate(cart)

        # 10% of the 50 EUR line only.
        assert result.discount_total.amount == Decimal("5.00")

    def test_excluded_categories_include_descendants(
        self, enable_promotions, make_cart
    ):
        from product.factories import ProductCategoryFactory

        parent = ProductCategoryFactory()
        child = ProductCategoryFactory(parent=parent)
        unrelated = ProductCategoryFactory()
        cart, products = make_cart([(100, 1), (50, 1)])
        products[0].category = child
        products[0].save(update_fields=["category"])
        products[1].category = unrelated
        products[1].save(update_fields=["category"])

        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(10),
        )
        promotion.excluded_categories.add(parent)

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == Decimal("5.00")

    def test_exclude_discounted_products(self, enable_promotions, make_cart):
        cart, products = make_cart([(100, 1), (50, 1)])
        products[0].discount_percent = Decimal(20)
        products[0].save(update_fields=["discount_percent"])

        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(10),
            exclude_discounted_products=True,
        )

        result = PromotionEngine.evaluate(cart)

        # Only the unmarked 50 EUR line participates.
        assert result.discount_total.amount == Decimal("5.00")


class TestMinQuantity:
    def test_min_quantity_gates_the_promotion(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(20, 2)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(10),
            min_quantity=3,
        )

        assert PromotionEngine.evaluate(cart).applied == []

        cart2, _ = make_cart([(20, 3)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(10),
            min_quantity=3,
        )
        assert PromotionEngine.evaluate(cart2).discount_total.amount > 0


class TestBxgy:
    def test_same_pool_buy_two_get_one_free(self, enable_promotions, make_cart):
        # Three 10 EUR units in the cart, buy 2 get 1 free.
        cart, _ = make_cart([(10, 3)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.BXGY,
            buy_quantity=2,
            get_quantity=1,
        )

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == Decimal("10.00")

    def test_same_pool_needs_full_group(self, enable_promotions, make_cart):
        cart, _ = make_cart([(10, 2)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.BXGY,
            buy_quantity=2,
            get_quantity=1,
        )

        assert PromotionEngine.evaluate(cart).applied == []

    def test_same_pool_discounts_cheapest_units(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(30, 1), (20, 1), (10, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.BXGY,
            buy_quantity=2,
            get_quantity=1,
        )

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == Decimal("10.00")

    def test_reward_pool_half_price(self, enable_promotions, make_cart):
        cart, products = make_cart([(40, 2), (16, 1)])
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.BXGY,
            buy_quantity=2,
            get_quantity=1,
            get_discount_percent=Decimal(50),
        )
        # Buy side = the 40 EUR product; reward pool = the 16 EUR one.
        promotion.products.clear()
        promotion.target_scope = "PRODUCTS"
        promotion.save(update_fields=["target_scope"])
        promotion.products.add(products[0])
        promotion.get_products.add(products[1])

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == Decimal("8.00")

    def test_multiple_applications(self, enable_promotions, make_cart):
        cart, _ = make_cart([(10, 6)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.BXGY,
            buy_quantity=2,
            get_quantity=1,
        )

        result = PromotionEngine.evaluate(cart)

        # 6 units / group of 3 = 2 applications = 2 free units.
        assert result.discount_total.amount == Decimal("20.00")


class TestFreeGift:
    def test_gift_entitlement_and_usage_row(self, enable_promotions, make_cart):
        from product.factories import ProductFactory

        gift_product = ProductFactory(
            price=Money(Decimal(15), "EUR"),
            discount_percent=Decimal(0),
            vat=None,
            stock=5,
            active=True,
        )
        cart, _ = make_cart([(100, 1)])
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_GIFT,
            get_quantity=1,
            min_subtotal=Money(Decimal(50), "EUR"),
        )
        promotion.get_products.add(gift_product)

        result = PromotionEngine.evaluate(cart)

        assert len(result.gift_items) == 1
        assert result.gift_items[0].product == gift_product
        assert result.gift_items[0].quantity == 1
        assert result.discount_total.amount == Decimal(0)
        assert (promotion, None) in [
            (promo, code) for promo, code in result.non_monetary
        ]

    def test_gift_without_configured_product_is_skipped(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_GIFT,
            get_quantity=1,
        )

        result = PromotionEngine.evaluate(cart)

        assert result.gift_items == []


class TestNearMiss:
    def test_near_miss_reports_remaining_amount(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(30, 1)])
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(10),
            min_subtotal=Money(Decimal(50), "EUR"),
        )

        result = PromotionEngine.evaluate(cart)

        assert len(result.near_miss) == 1
        entry = result.near_miss[0]
        assert entry.promotion == promotion
        assert entry.remaining_amount == Decimal("20.00")

    def test_no_near_miss_when_threshold_met(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(60, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(10),
            min_subtotal=Money(Decimal(50), "EUR"),
        )

        result = PromotionEngine.evaluate(cart)

        assert result.near_miss == []
        assert result.discount_total.amount > 0

    def test_bxgy_short_of_its_group_reports_remaining_units(
        self, enable_promotions, make_cart
    ):
        """Demo store, 2026-10-10: two cables in the cart showed nothing
        about "2+1"; the offer appeared only once the third was added."""
        cart, products = make_cart([(10, 2)])
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.BXGY,
            buy_quantity=2,
            get_quantity=1,
            get_discount_percent=Decimal(100),
            target_scope=TargetScope.PRODUCTS,
        )
        promotion.products.set(products)

        result = PromotionEngine.evaluate(cart)

        [entry] = result.near_miss
        assert entry.promotion == promotion
        assert entry.remaining_quantity == 1
        assert entry.remaining_amount is None
        assert result.discount_total.amount == 0

    def test_bxgy_with_a_full_group_applies_without_a_teaser(
        self, enable_promotions, make_cart
    ):
        cart, products = make_cart([(10, 3)])
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.BXGY,
            buy_quantity=2,
            get_quantity=1,
            get_discount_percent=Decimal(100),
            target_scope=TargetScope.PRODUCTS,
        )
        promotion.products.set(products)

        result = PromotionEngine.evaluate(cart)

        assert result.near_miss == []
        assert result.discount_total == Money(Decimal("10.00"), "EUR")

    def test_bxgy_with_none_of_its_products_in_the_cart_is_silent(
        self, enable_promotions, make_cart
    ):
        cart, _products = make_cart([(10, 2)])
        elsewhere = [ProductFactory()]
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.BXGY,
            buy_quantity=2,
            get_quantity=1,
            get_discount_percent=Decimal(100),
            target_scope=TargetScope.PRODUCTS,
        )
        promotion.products.set(elsewhere)

        assert PromotionEngine.evaluate(cart).near_miss == []

    def test_min_quantity_short_reports_remaining_units(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(10, 2)])
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(5),
            min_quantity=3,
        )

        [entry] = PromotionEngine.evaluate(cart).near_miss

        assert entry.promotion == promotion
        assert entry.remaining_quantity == 1
        assert entry.remaining_amount is None

    def test_both_minimums_short_is_not_a_single_teaser(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(10, 2)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(5),
            min_quantity=3,
            min_subtotal=Money(Decimal(50), "EUR"),
        )

        assert PromotionEngine.evaluate(cart).near_miss == []


class TestNotApplicableCode:
    def test_attached_code_left_without_its_products_does_not_block(
        self, enable_promotions, make_cart
    ):
        """The shopper applied a code, then removed the item it covered.
        It discounts nothing either way, so order creation must not
        refuse the order over it (the preview already showed no saving).
        """

        cart, _products = make_cart([(10, 1)])
        elsewhere = [ProductFactory()]
        promotion = PromotionFactory(
            trigger=PromotionTrigger.CODE,
            benefit_value=Decimal(10),
            target_scope=TargetScope.PRODUCTS,
        )
        promotion.products.set(elsewhere)
        code = PromotionCodeFactory(promotion=promotion, code="GONE")
        CartPromotionCode.objects.create(cart=cart, code=code)

        result = PromotionEngine.evaluate(cart)

        assert result.rejected == [
            ("GONE", str(CouponRejectionReason.NOT_APPLICABLE))
        ]
        assert result.blocking_rejections == []


def _sellable_product(price, *, stock=1000, active=True):
    return ProductFactory(
        price=Money(Decimal(str(price)), "EUR"),
        discount_percent=Decimal(0),
        vat=None,
        stock=stock,
        active=active,
    )


def _reward_pool(buy_products, reward_products, *, trigger, **kwargs):
    """A "buy a case, get its screen protector at -50%" offer."""
    promotion = PromotionFactory(
        trigger=trigger,
        benefit_type=BenefitType.BXGY,
        buy_quantity=1,
        get_quantity=1,
        get_discount_percent=Decimal(50),
        target_scope=TargetScope.PRODUCTS,
        **kwargs,
    )
    promotion.products.set(buy_products)
    promotion.get_products.set(reward_products)
    return promotion


class TestTeaserIsAPromise:
    """A teaser is shown only when doing what it says gets the offer."""

    def test_withheld_when_a_better_non_stackable_offer_would_win(
        self, enable_promotions, make_cart
    ):
        """Demo store, 2026-10-10: two cables teased "-5% from 3 items",
        but the third cable unlocks a non-stackable "2+1" worth more, so
        the -5% the teaser promised is never granted."""
        cart, products = make_cart([(10, 2)])
        five_percent = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(5),
            min_quantity=3,
            stackable=True,
        )
        two_plus_one = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.BXGY,
            buy_quantity=2,
            get_quantity=1,
            get_discount_percent=Decimal(100),
            target_scope=TargetScope.PRODUCTS,
            stackable=False,
        )
        two_plus_one.products.set(products)

        result = PromotionEngine.evaluate(cart)

        assert [entry.promotion for entry in result.near_miss] == [two_plus_one]
        assert five_percent not in {e.promotion for e in result.near_miss}

    def test_reward_pool_teaser_names_the_rewards_that_can_be_bought(
        self, enable_promotions, make_cart
    ):
        cart, [case] = make_cart([(20, 1)])
        sellable = _sellable_product(8)
        inactive = _sellable_product(6, active=False)
        sold_out = _sellable_product(5, stock=0)
        promotion = _reward_pool(
            [case],
            [sellable, inactive, sold_out],
            trigger=PromotionTrigger.AUTOMATIC,
        )

        [entry] = PromotionEngine.evaluate(cart).near_miss

        assert entry.promotion == promotion
        assert entry.reward_products == [sellable]
        assert entry.remaining_quantity == 1
        assert entry.remaining_amount is None

    def test_one_reward_unit_unlocks_a_multi_unit_reward(
        self, enable_promotions, make_cart
    ):
        """ "Buy 1, get 2 at -50%": the first reward unit already earns
        the offer, so the teaser asks for one."""
        cart, [case] = make_cart([(20, 1)])
        promotion = _reward_pool(
            [case], [_sellable_product(8)], trigger=PromotionTrigger.AUTOMATIC
        )
        promotion.get_quantity = 2
        promotion.save(update_fields=["get_quantity"])

        [entry] = PromotionEngine.evaluate(cart).near_miss

        assert entry.remaining_quantity == 1

    def test_reward_pool_short_of_its_buy_side_asks_for_units(
        self, enable_promotions, make_cart
    ):
        cart, [case, protector] = make_cart([(20, 1), (8, 1)])
        promotion = _reward_pool(
            [case], [protector], trigger=PromotionTrigger.AUTOMATIC
        )
        promotion.buy_quantity = 2
        promotion.save(update_fields=["buy_quantity"])

        [entry] = PromotionEngine.evaluate(cart).near_miss

        assert entry.remaining_quantity == 1
        assert entry.reward_products == []

    def test_reward_pool_short_of_both_sides_is_not_a_single_teaser(
        self, enable_promotions, make_cart
    ):
        """One more case alone would still leave the reward missing, so
        "add 1 more" would be a promise the cart then breaks."""
        cart, [case] = make_cart([(20, 1)])
        promotion = _reward_pool(
            [case],
            [_sellable_product(8)],
            trigger=PromotionTrigger.AUTOMATIC,
        )
        promotion.buy_quantity = 2
        promotion.save(update_fields=["buy_quantity"])

        assert PromotionEngine.evaluate(cart).near_miss == []

    def test_reward_pool_without_a_buyable_reward_is_silent(
        self, enable_promotions, make_cart
    ):
        cart, [case] = make_cart([(20, 1)])
        _reward_pool(
            [case],
            [_sellable_product(8, stock=0)],
            trigger=PromotionTrigger.AUTOMATIC,
        )

        assert PromotionEngine.evaluate(cart).near_miss == []

    def test_gift_teaser_withheld_while_the_gift_is_sold_out(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(30, 1)])
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_GIFT,
            get_quantity=1,
            min_subtotal=Money(Decimal(50), "EUR"),
        )
        promotion.get_products.add(_sellable_product(15, stock=0))

        assert PromotionEngine.evaluate(cart).near_miss == []


class TestACodeMustChangeTheOutcome:
    """apply() accepts a code only when it takes something off."""

    def test_gift_code_with_a_sold_out_gift_is_refused_without_blocking(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        promotion = PromotionFactory(
            trigger=PromotionTrigger.CODE,
            benefit_type=BenefitType.FREE_GIFT,
            get_quantity=1,
        )
        promotion.get_products.add(_sellable_product(15, stock=0))
        code = PromotionCodeFactory(promotion=promotion, code="GIFT")
        CartPromotionCode.objects.create(cart=cart, code=code)

        result = PromotionEngine.evaluate(cart)

        assert result.gift_items == []
        assert result.rejected == [
            ("GIFT", str(CouponRejectionReason.GIFT_UNAVAILABLE))
        ]
        # The paid goods are never held hostage by a sold-out freebie.
        assert result.blocking_rejections == []

    def test_free_shipping_code_redundant_with_an_automatic_is_refused(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        automatic = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_SHIPPING,
        )
        code = PromotionCodeFactory(
            promotion=PromotionFactory(
                trigger=PromotionTrigger.CODE,
                benefit_type=BenefitType.FREE_SHIPPING,
            ),
            code="SHIPFREE",
        )
        CartPromotionCode.objects.create(cart=cart, code=code)

        result = PromotionEngine.evaluate(cart)

        assert result.free_shipping is True
        assert result.non_monetary == [(automatic, None)]
        assert result.rejected == [
            ("SHIPFREE", str(CouponRejectionReason.COMBINATION_DISALLOWED))
        ]
        assert result.blocking_rejections == []

    def test_code_the_clamp_leaves_with_nothing_is_refused(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(50, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(50),
            stackable=True,
            priority=0,
        )
        code = PromotionCodeFactory(
            promotion=PromotionFactory(
                trigger=PromotionTrigger.CODE,
                benefit_type=BenefitType.FIXED_AMOUNT,
                benefit_value=Decimal(10),
                stackable=True,
                priority=10,
            ),
            code="LATE",
        )
        CartPromotionCode.objects.create(cart=cart, code=code)

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total == Money(Decimal("50.00"), "EUR")
        assert "LATE" not in {e.code.code for e in result.applied if e.code}
        assert result.rejected == [
            ("LATE", str(CouponRejectionReason.COMBINATION_DISALLOWED))
        ]

    def test_reward_pool_code_without_the_reward_is_minimum_not_met(
        self, enable_promotions, make_cart
    ):
        cart, [case] = make_cart([(20, 1)])
        promotion = _reward_pool(
            [case], [_sellable_product(8)], trigger=PromotionTrigger.CODE
        )
        code = PromotionCodeFactory(promotion=promotion, code="CASEGLASS")
        CartPromotionCode.objects.create(cart=cart, code=code)

        result = PromotionEngine.evaluate(cart)

        assert result.rejected == [
            ("CASEGLASS", str(CouponRejectionReason.MINIMUM_NOT_MET))
        ]

    def test_reward_pool_code_with_no_buyable_reward_is_not_applicable(
        self, enable_promotions, make_cart
    ):
        cart, [case] = make_cart([(20, 1)])
        promotion = _reward_pool(
            [case],
            [_sellable_product(8, active=False)],
            trigger=PromotionTrigger.CODE,
        )
        code = PromotionCodeFactory(promotion=promotion, code="CASEGLASS")
        CartPromotionCode.objects.create(cart=cart, code=code)

        result = PromotionEngine.evaluate(cart)

        assert result.rejected == [
            ("CASEGLASS", str(CouponRejectionReason.NOT_APPLICABLE))
        ]
