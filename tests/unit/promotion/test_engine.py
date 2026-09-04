"""PromotionEngine evaluation semantics.

The engine is the single money authority for promotion discounts —
cart preview, payment-intent amount, order-create verification and the
order application all read it, so these tests pin the arithmetic and
the eligibility/stacking policies rather than any one endpoint.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone
from djmoney.money import Money

from promotion.enum import (
    BenefitType,
    CouponRejectionReason,
    PromotionTrigger,
    TargetScope,
)
from promotion.factories import PromotionCodeFactory, PromotionFactory
from promotion.models import CartPromotionCode, PromotionRedemption
from promotion.services import PromotionEngine

pytestmark = pytest.mark.django_db


def _attach_code(cart, code):
    return CartPromotionCode.objects.create(cart=cart, code=code)


class TestEnabledGate:
    def test_disabled_setting_returns_empty_result(self, make_cart):
        cart, _ = make_cart([(50, 1)])
        PromotionFactory(trigger=PromotionTrigger.AUTOMATIC)

        result = PromotionEngine.evaluate(cart)

        assert result.applied == []
        assert result.discount_total.amount == 0
        assert result.free_shipping is False


class TestAutomaticPromotions:
    def test_percentage_on_order_scope(self, enable_promotions, make_cart):
        cart, _ = make_cart([(40, 1), (10, 2)])  # items total 60
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(10),
        )

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == Decimal("6.00")

    def test_window_excludes_not_started_and_ended(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(50, 1)])
        now = timezone.now()
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            starts_at=now + timezone.timedelta(days=1),
        )
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            ends_at=now - timezone.timedelta(days=1),
        )
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            is_active=False,
        )

        result = PromotionEngine.evaluate(cart)

        assert result.applied == []

    def test_min_subtotal_condition(self, enable_promotions, make_cart):
        cart, _ = make_cart([(30, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            min_subtotal=Money(Decimal(50), "EUR"),
        )

        assert PromotionEngine.evaluate(cart).applied == []

    def test_first_order_only_skips_returning_customer(
        self, enable_promotions, make_cart
    ):
        from order.factories.order import OrderFactory
        from user.factories import UserAccountFactory

        returning = UserAccountFactory()
        OrderFactory(user=returning)
        fresh = UserAccountFactory()

        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC, first_order_only=True
        )

        cart_returning, _ = make_cart([(50, 1)], user=returning)
        cart_fresh, _ = make_cart([(50, 1)], user=fresh)

        assert (
            PromotionEngine.evaluate(cart_returning, user=returning).applied
            == []
        )
        assert (
            PromotionEngine.evaluate(
                cart_fresh, user=fresh
            ).discount_total.amount
            > 0
        )

    def test_first_order_only_sees_the_shoppers_guest_history(
        self, enable_promotions, make_cart
    ):
        """Registering after a guest checkout must not reset eligibility.

        A guest order is written with `user=None, email=...`, and nothing
        backfills the account onto it when the shopper later registers.
        Checking only `user=` once they are authenticated therefore could
        not see their own history, and handed the first-order discount to
        a returning customer.
        """
        from order.factories.order import OrderFactory
        from user.factories import UserAccountFactory

        shopper = UserAccountFactory(email="repeat@example.com")
        # The earlier purchase, made before they had an account.
        OrderFactory(user=None, email="repeat@example.com")

        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC, first_order_only=True
        )
        cart, _ = make_cart([(50, 1)], user=shopper)

        assert PromotionEngine.evaluate(cart, user=shopper).applied == []

    def test_per_customer_limit_counts_guest_redemptions(
        self, enable_promotions, make_cart
    ):
        """Same identity switch, against `usage_limit_per_customer`."""
        from promotion.models.redemption import PromotionRedemption
        from user.factories import UserAccountFactory

        shopper = UserAccountFactory(email="repeat2@example.com")
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            usage_limit_per_customer=1,
        )
        # Redeemed once as a guest, under the same email.
        PromotionRedemption.objects.create(
            promotion=promotion,
            user=None,
            email="repeat2@example.com",
            amount=Money(Decimal(5), "EUR"),
        )

        cart, _ = make_cart([(50, 1)], user=shopper)

        assert PromotionEngine.evaluate(cart, user=shopper).applied == []

    def test_first_order_only_guest_unknown_identity_is_conservative(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(50, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC, first_order_only=True
        )

        # Anonymous preview must not show a discount the checkout
        # (which carries the email) could take away.
        assert PromotionEngine.evaluate(cart).applied == []


class TestCodePromotions:
    def test_attached_code_applies(self, enable_promotions, make_cart):
        cart, _ = make_cart([(100, 1)])
        code = PromotionCodeFactory(
            promotion=PromotionFactory(benefit_value=Decimal(15))
        )
        _attach_code(cart, code)

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == Decimal("15.00")
        assert result.applied[0].code == code

    def test_inactive_code_rejected_invalid(self, enable_promotions, make_cart):
        cart, _ = make_cart([(100, 1)])
        code = PromotionCodeFactory(is_active=False)
        _attach_code(cart, code)

        result = PromotionEngine.evaluate(cart)

        assert (
            code.code,
            str(CouponRejectionReason.INVALID),
        ) in result.rejected

    def test_usage_limit_total_exhausted(self, enable_promotions, make_cart):
        cart, _ = make_cart([(100, 1)])
        promotion = PromotionFactory(usage_limit_total=1)
        code = PromotionCodeFactory(promotion=promotion)
        PromotionRedemption.objects.create(
            promotion=promotion,
            code=code,
            amount=Money(Decimal(5), "EUR"),
        )
        _attach_code(cart, code)

        result = PromotionEngine.evaluate(cart)

        assert result.applied == []
        assert (
            code.code,
            str(CouponRejectionReason.USAGE_LIMIT_REACHED),
        ) in result.rejected

    def test_per_customer_limit_by_email(self, enable_promotions, make_cart):
        cart, _ = make_cart([(100, 1)])
        promotion = PromotionFactory(usage_limit_per_customer=1)
        code = PromotionCodeFactory(promotion=promotion)
        PromotionRedemption.objects.create(
            promotion=promotion,
            code=code,
            email="shopper@example.com",
            amount=Money(Decimal(5), "EUR"),
        )
        _attach_code(cart, code)

        blocked = PromotionEngine.evaluate(cart, email="Shopper@Example.com")
        allowed = PromotionEngine.evaluate(cart, email="other@example.com")

        assert blocked.applied == []
        assert allowed.discount_total.amount > 0

    def test_per_code_usage_limit(self, enable_promotions, make_cart):
        cart, _ = make_cart([(100, 1)])
        promotion = PromotionFactory()
        code = PromotionCodeFactory(promotion=promotion, usage_limit=1)
        PromotionRedemption.objects.create(
            promotion=promotion,
            code=code,
            amount=Money(Decimal(5), "EUR"),
        )
        _attach_code(cart, code)

        result = PromotionEngine.evaluate(cart)

        assert result.applied == []

    def test_personal_code_enforced(self, enable_promotions, make_cart):
        from user.factories import UserAccountFactory

        owner = UserAccountFactory()
        stranger = UserAccountFactory()
        code = PromotionCodeFactory(
            promotion=PromotionFactory(), assigned_to=owner
        )

        cart_owner, _ = make_cart([(100, 1)], user=owner)
        _attach_code(cart_owner, code)
        cart_stranger, _ = make_cart([(100, 1)], user=stranger)
        _attach_code(cart_stranger, code)

        assert (
            PromotionEngine.evaluate(
                cart_owner, user=owner
            ).discount_total.amount
            > 0
        )
        stranger_result = PromotionEngine.evaluate(cart_stranger, user=stranger)
        assert stranger_result.applied == []
        assert (
            code.code,
            str(CouponRejectionReason.USER_INELIGIBLE),
        ) in stranger_result.rejected

    def test_personal_code_guest_email_match(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        code = PromotionCodeFactory(
            promotion=PromotionFactory(),
            assigned_to_email="vip@example.com",
        )
        _attach_code(cart, code)

        assert (
            PromotionEngine.evaluate(
                cart, email="VIP@example.com"
            ).discount_total.amount
            > 0
        )
        assert (
            PromotionEngine.evaluate(cart, email="someone@else.com").applied
            == []
        )


class TestTargetScopes:
    def test_products_scope_discounts_matching_lines_only(
        self, enable_promotions, make_cart
    ):
        cart, products = make_cart([(100, 1), (50, 2)])
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            target_scope=TargetScope.PRODUCTS,
            benefit_value=Decimal(10),
        )
        promotion.products.add(products[0])

        result = PromotionEngine.evaluate(cart)

        # 10% of the 100 EUR line only.
        assert result.discount_total.amount == Decimal("10.00")

    def test_categories_scope_includes_descendants(
        self, enable_promotions, make_cart
    ):
        from product.factories import ProductCategoryFactory

        parent = ProductCategoryFactory()
        child = ProductCategoryFactory(parent=parent)
        cart, products = make_cart([(80, 1)])
        products[0].category = child
        products[0].save(update_fields=["category"])

        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            target_scope=TargetScope.CATEGORIES,
            benefit_value=Decimal(25),
        )
        promotion.categories.add(parent)

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == Decimal("20.00")


class TestBenefits:
    def test_fixed_amount_clamped_to_matching_base(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(30, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(100),
        )

        assert PromotionEngine.evaluate(cart).discount_total.amount == Decimal(
            "30.00"
        )

    def test_max_discount_amount_ceiling(self, enable_promotions, make_cart):
        cart, _ = make_cart([(200, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(50),
            max_discount_amount=Money(Decimal(20), "EUR"),
        )

        assert PromotionEngine.evaluate(cart).discount_total.amount == Decimal(
            "20.00"
        )

    def test_free_shipping_sets_flag_and_combines(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_SHIPPING,
            stackable=False,
        )
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(10),
        )

        result = PromotionEngine.evaluate(cart)

        assert result.free_shipping is True
        # Free shipping never competes with the monetary promotion.
        assert result.discount_total.amount == Decimal("10.00")


class TestStacking:
    def test_stackables_sum_beats_weaker_non_stackable(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(10),
            stackable=True,
        )
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(8),
            stackable=True,
        )
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(15),
            stackable=False,
        )

        result = PromotionEngine.evaluate(cart)

        # 10% + 8 = 18 beats the single 15.
        assert result.discount_total.amount == Decimal("18.00")
        assert len(result.applied) == 2

    def test_strong_non_stackable_wins_alone(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(5),
            stackable=True,
        )
        winner = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(25),
            stackable=False,
        )

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == Decimal("25.00")
        assert result.applied[0].promotion == winner

    def test_losing_code_reports_combination_disallowed(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(30),
            stackable=True,
        )
        code = PromotionCodeFactory(
            promotion=PromotionFactory(
                benefit_type=BenefitType.FIXED_AMOUNT,
                benefit_value=Decimal(5),
                stackable=False,
            )
        )
        _attach_code(cart, code)

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == Decimal("30.00")
        assert (
            code.code,
            str(CouponRejectionReason.COMBINATION_DISALLOWED),
        ) in result.rejected
        # Combination losses never block order creation.
        assert result.blocking_rejections == []

    def test_total_clamped_at_items_total(self, enable_promotions, make_cart):
        cart, _ = make_cart([(20, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(15),
            stackable=True,
        )
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(15),
            stackable=True,
        )

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == Decimal("20.00")


class TestRecord:
    def test_record_writes_redemptions_and_metadata(
        self, enable_promotions, make_cart
    ):
        from order.factories.order import OrderFactory

        cart, _ = make_cart([(100, 1)])
        code = PromotionCodeFactory(
            promotion=PromotionFactory(benefit_value=Decimal(10))
        )
        _attach_code(cart, code)
        result = PromotionEngine.evaluate(cart)

        order = OrderFactory()
        total = PromotionEngine.record(order, result)

        assert total.amount == Decimal("10.00")
        redemption = PromotionRedemption.objects.get(order=order)
        assert redemption.code == code
        assert redemption.amount.amount == Decimal("10.00")
        assert order.metadata["promotions"][0]["code"] == code.code
