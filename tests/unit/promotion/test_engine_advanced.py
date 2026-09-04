"""BXGY / FREE_GIFT / exclusions / min-quantity / near-miss semantics."""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from promotion.enum import BenefitType, PromotionTrigger
from promotion.factories import PromotionFactory
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
