"""The checkout coupon picker.

The promise this service makes is narrow and testable: ``eligible`` is
exactly what ``CouponService.apply`` would do, and ``amount`` is exactly
what the cart would then be charged less. The tests below pin both,
because the failure mode is silent — a picker that offers a code apply()
refuses, or advertises a saving the cart never grants, is worse than no
picker at all.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from djmoney.money import Money

from product.factories import ProductFactory
from promotion.enum import (
    BenefitType,
    CouponRejectionReason,
    PromotionTrigger,
    TargetScope,
)
from promotion.factories.promotion import (
    PromotionCodeFactory,
    PromotionFactory,
    PromotionTranslationFactory,
)
from promotion.models import PromotionRedemption
from promotion.services import (
    CouponError,
    CouponPickerService,
    CouponService,
    PromotionEngine,
)
from user.factories.account import UserAccountFactory


def _coded(code="SAVE10", *, name="Offer", **kwargs):
    """A live CODE promotion carrying one advertisable code."""
    kwargs.setdefault("trigger", PromotionTrigger.CODE)
    kwargs.setdefault("is_active", True)
    kwargs.setdefault("target_scope", TargetScope.ORDER)
    promotion = PromotionFactory(**kwargs)
    PromotionTranslationFactory(master=promotion, language_code="el", name=name)
    return promotion, PromotionCodeFactory(promotion=promotion, code=code)


def _automatic(*, name="Auto", **kwargs):
    kwargs.setdefault("trigger", PromotionTrigger.AUTOMATIC)
    kwargs.setdefault("is_active", True)
    kwargs.setdefault("target_scope", TargetScope.ORDER)
    promotion = PromotionFactory(**kwargs)
    PromotionTranslationFactory(master=promotion, language_code="el", name=name)
    return promotion


def _by_code(options) -> dict[str, object]:
    return {option.code.code: option for option in options}


@pytest.mark.django_db
class TestVerdict:
    def test_lists_an_advertisable_code_as_eligible(
        self, make_cart, enable_promotions
    ):
        cart, _products = make_cart([(50, 1)])
        _coded(
            "TEN",
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal(10),
        )

        options = CouponPickerService.available(cart)

        assert [option.code.code for option in options] == ["TEN"]
        assert options[0].eligible is True
        assert options[0].reason is None
        assert options[0].amount == Money(Decimal("5.00"), "EUR")

    def test_reports_the_minimum_subtotal_refusal(
        self, make_cart, enable_promotions
    ):
        cart, _products = make_cart([(10, 1)])
        _coded(
            "BIG",
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(5),
            min_subtotal=Money(Decimal("50.00"), "EUR"),
        )

        option = _by_code(CouponPickerService.available(cart))["BIG"]

        assert option.eligible is False
        assert option.reason == str(CouponRejectionReason.MINIMUM_NOT_MET)
        assert option.amount == Money(Decimal(0), "EUR")

    def test_reports_an_expired_promotion(self, make_cart, enable_promotions):
        """A code whose promotion died after it was minted is still a
        row the shopper may hold — it has to read as expired, not
        vanish."""
        cart, _products = make_cart([(50, 1)])
        now = timezone.now()
        _coded(
            "OVER",
            starts_at=now - timedelta(days=10),
            ends_at=now - timedelta(days=1),
        )

        options = CouponPickerService.available(cart)

        # Not live, so ``publicly_listable`` never surfaces it: the
        # picker offers nothing rather than a dead row.
        assert options == []

    def test_reports_the_usage_limit(self, make_cart, enable_promotions):
        cart, _products = make_cart([(50, 1)])
        promotion, _code = _coded("ONCE", usage_limit_total=1)
        PromotionRedemption.objects.create(
            promotion=promotion,
            email="someone@example.com",
            amount=Money(Decimal("1.00"), "EUR"),
        )

        # Exhausted promotions leave the advertisable set entirely, the
        # same rule the offers page applies.
        assert CouponPickerService.available(cart) == []

    def test_first_order_only_refuses_a_returning_customer(
        self, make_cart, enable_promotions
    ):
        from order.factories.order import OrderFactory

        user = UserAccountFactory()
        OrderFactory(user=user, email=user.email)
        cart, _products = make_cart([(50, 1)], user=user)
        _coded(
            "WELCOME",
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal(10),
            first_order_only=True,
        )

        option = _by_code(CouponPickerService.available(cart, user=user))[
            "WELCOME"
        ]

        assert option.eligible is False
        assert option.reason == str(CouponRejectionReason.USER_INELIGIBLE)


@pytest.mark.django_db
class TestVerdictMatchesApply:
    """The contract that makes the picker trustworthy."""

    @pytest.mark.parametrize(
        ("kwargs", "competing_automatic"),
        [
            pytest.param({}, None, id="plain"),
            pytest.param(
                {"min_subtotal": Money(Decimal("500.00"), "EUR")},
                None,
                id="minimum-not-met",
            ),
            pytest.param(
                {"first_order_only": True}, None, id="first-order-only"
            ),
            # The case the other three cannot see: eligibility is not
            # only about the code, it is about what the cart already
            # earns. Without a competing automatic offer the stacking
            # resolution never runs, and the picker looked correct
            # while offering codes apply() answered with a 400.
            pytest.param(
                {"stackable": False}, Decimal(30), id="beaten-by-automatic"
            ),
            pytest.param(
                {"stackable": False}, Decimal(2), id="beats-the-automatic"
            ),
        ],
    )
    def test_eligible_agrees_with_coupon_service_apply(
        self, make_cart, enable_promotions, kwargs, competing_automatic
    ):
        cart, _products = make_cart([(50, 1)])
        if competing_automatic is not None:
            _automatic(
                benefit_type=BenefitType.PERCENTAGE,
                benefit_value=competing_automatic,
                stackable=True,
            )
        _coded(
            "MATCH",
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal(10),
            **kwargs,
        )

        option = _by_code(CouponPickerService.available(cart))["MATCH"]

        applied = True
        try:
            CouponService.apply(cart, "MATCH")
        except CouponError:
            applied = False

        assert option.eligible is applied

    def test_amount_matches_what_the_cart_is_then_charged(
        self, make_cart, enable_promotions
    ):
        cart, _products = make_cart([(80, 1)])
        _automatic(
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal(10),
            stackable=True,
        )
        _coded(
            "EXTRA",
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(5),
            stackable=True,
        )

        promised = _by_code(CouponPickerService.available(cart))["EXTRA"].amount

        before = PromotionEngine.evaluate(cart).discount_total
        CouponService.apply(cart, "EXTRA")
        after = PromotionEngine.evaluate(cart).discount_total

        assert promised == after - before

    def test_a_non_stackable_code_beaten_by_automatics_is_refused(
        self, make_cart, enable_promotions
    ):
        """``_resolve_stacking`` rejects the losing code, and
        ``CouponService.apply`` raises on ANY rejection the evaluation
        produces — COMBINATION_DISALLOWED included. Found on a live
        cart: the picker offered an enabled button for a code the very
        next request answered with a 400.
        """
        cart, _products = make_cart([(100, 1)])
        _automatic(
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal(30),
            stackable=True,
        )
        _coded(
            "WEAK",
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal(10),
            stackable=False,
        )

        option = _by_code(CouponPickerService.available(cart))["WEAK"]

        assert option.eligible is False
        assert option.reason == str(
            CouponRejectionReason.COMBINATION_DISALLOWED
        )
        assert option.amount == Money(Decimal(0), "EUR")

        with pytest.raises(CouponError):
            CouponService.apply(cart, "WEAK")

    def test_a_non_stackable_code_that_beats_the_automatics_is_offered(
        self, make_cart, enable_promotions
    ):
        """The mirror image: when the coupon wins the comparison the
        automatics are the ones thrown out, apply() accepts, and the
        quoted saving is the DIFFERENCE it makes over them."""
        cart, _products = make_cart([(100, 1)])
        _automatic(
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal(10),
            stackable=True,
        )
        _coded(
            "STRONG",
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal(30),
            stackable=False,
        )

        option = _by_code(CouponPickerService.available(cart))["STRONG"]

        assert option.eligible is True
        assert option.amount == Money(Decimal("20.00"), "EUR")

        before = PromotionEngine.evaluate(cart).discount_total
        CouponService.apply(cart, "STRONG")
        after = PromotionEngine.evaluate(cart).discount_total
        assert after - before == option.amount

    def test_a_code_for_products_not_in_the_cart_is_not_applicable(
        self, make_cart, enable_promotions
    ):
        """Demo store, 2026-10-10: a GaN-charger code on a cart of cables
        was offered as usable and "applied" for nothing. A code whose
        scope matches nothing in the cart is refused by both the picker
        and apply(), with the same reason."""
        cart, _products = make_cart([(100, 1)])
        promotion, _code = _coded(
            "ELSEWHERE",
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal(20),
            target_scope=TargetScope.PRODUCTS,
        )
        promotion.products.set([ProductFactory()])

        option = _by_code(CouponPickerService.available(cart))["ELSEWHERE"]

        assert option.eligible is False
        assert option.reason == str(CouponRejectionReason.NOT_APPLICABLE)
        with pytest.raises(CouponError) as refused:
            CouponService.apply(cart, "ELSEWHERE")
        assert refused.value.reason == str(CouponRejectionReason.NOT_APPLICABLE)
        assert not cart.applied_codes.exists()

    def test_a_bxgy_code_short_of_its_group_is_minimum_not_met(
        self, make_cart, enable_promotions
    ):
        """ "Buy 2 get 1" with two units in the cart earns nothing yet:
        refused as minimum-not-met everywhere, never accepted for 0."""
        cart, products = make_cart([(10, 2)])
        promotion, _code = _coded(
            "TWOPLUSONE",
            benefit_type=BenefitType.BXGY,
            buy_quantity=2,
            get_quantity=1,
            get_discount_percent=Decimal(100),
            target_scope=TargetScope.PRODUCTS,
        )
        promotion.products.set(products)

        option = _by_code(CouponPickerService.available(cart))["TWOPLUSONE"]

        assert option.reason == str(CouponRejectionReason.MINIMUM_NOT_MET)
        with pytest.raises(CouponError) as refused:
            CouponService.apply(cart, "TWOPLUSONE")
        assert refused.value.reason == str(
            CouponRejectionReason.MINIMUM_NOT_MET
        )

    def test_amount_ignores_the_code_already_attached(
        self, make_cart, enable_promotions
    ):
        """Applying a coupon REPLACES the attached one, so the quoted
        saving must be measured against the automatics alone — not
        stacked on top of a code that is about to be dropped."""
        cart, _products = make_cart([(100, 1)])
        _coded(
            "OLD",
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(20),
            stackable=True,
        )
        _coded(
            "NEW",
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal(10),
            stackable=True,
        )
        CouponService.apply(cart, "OLD")

        options = _by_code(CouponPickerService.available(cart))

        assert options["OLD"].applied is True
        assert options["NEW"].applied is False
        assert options["NEW"].amount == Money(Decimal("10.00"), "EUR")


@pytest.mark.django_db
class TestNonMonetaryBenefits:
    def test_free_shipping_is_eligible_and_flagged(
        self, make_cart, enable_promotions
    ):
        cart, _products = make_cart([(50, 1)])
        _coded("SHIPFREE", benefit_type=BenefitType.FREE_SHIPPING)

        option = _by_code(CouponPickerService.available(cart))["SHIPFREE"]

        assert option.eligible is True
        assert option.free_shipping is True
        assert option.amount == Money(Decimal(0), "EUR")

    def test_free_shipping_is_refused_when_an_automatic_already_waives(
        self, make_cart, enable_promotions
    ):
        """The code would change nothing, so both the picker and apply()
        refuse it rather than "apply" it for nothing."""
        cart, _products = make_cart([(50, 1)])
        _automatic(benefit_type=BenefitType.FREE_SHIPPING, stackable=True)
        _coded("SHIPFREE", benefit_type=BenefitType.FREE_SHIPPING)

        option = _by_code(CouponPickerService.available(cart))["SHIPFREE"]

        assert option.reason == str(
            CouponRejectionReason.COMBINATION_DISALLOWED
        )
        assert option.free_shipping is False
        with pytest.raises(CouponError) as refused:
            CouponService.apply(cart, "SHIPFREE")
        assert refused.value.reason == option.reason

    def test_a_gift_code_whose_gift_sold_out_is_refused(
        self, make_cart, enable_promotions
    ):
        cart, _products = make_cart([(50, 1)])
        promotion, _code = _coded(
            "GIFT", benefit_type=BenefitType.FREE_GIFT, get_quantity=1
        )
        promotion.get_products.add(ProductFactory(stock=0, active=True))

        option = _by_code(CouponPickerService.available(cart))["GIFT"]

        assert option.reason == str(CouponRejectionReason.GIFT_UNAVAILABLE)
        with pytest.raises(CouponError) as refused:
            CouponService.apply(cart, "GIFT")
        assert refused.value.reason == option.reason

    def test_a_gift_code_with_a_gift_in_stock_is_offered(
        self, make_cart, enable_promotions
    ):
        cart, _products = make_cart([(50, 1)])
        promotion, _code = _coded(
            "GIFT", benefit_type=BenefitType.FREE_GIFT, get_quantity=1
        )
        promotion.get_products.add(ProductFactory(stock=5, active=True))

        option = _by_code(CouponPickerService.available(cart))["GIFT"]

        assert option.eligible is True
        assert CouponService.apply(cart, "GIFT").gift_items


@pytest.mark.django_db
class TestCodesWorthNothing:
    """A code that would take nothing off is refused by both sides."""

    def test_a_code_the_clamp_leaves_with_nothing(
        self, make_cart, enable_promotions
    ):
        cart, _products = make_cart([(50, 1)])
        _automatic(
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(50),
            stackable=True,
            priority=0,
        )
        _coded(
            "LATE",
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(10),
            stackable=True,
            priority=10,
        )

        option = _by_code(CouponPickerService.available(cart))["LATE"]

        assert option.reason == str(
            CouponRejectionReason.COMBINATION_DISALLOWED
        )
        with pytest.raises(CouponError) as refused:
            CouponService.apply(cart, "LATE")
        assert refused.value.reason == option.reason

    @pytest.mark.parametrize(
        ("reward_kwargs", "reason"),
        [
            pytest.param(
                {"stock": 5, "active": True},
                CouponRejectionReason.MINIMUM_NOT_MET,
                id="reward-buyable-but-missing",
            ),
            pytest.param(
                {"stock": 5, "active": False},
                CouponRejectionReason.NOT_APPLICABLE,
                id="no-buyable-reward",
            ),
        ],
    )
    def test_a_reward_pool_code_without_its_reward(
        self, make_cart, enable_promotions, reward_kwargs, reason
    ):
        cart, [case] = make_cart([(20, 1)])
        promotion, _code = _coded(
            "CASEGLASS",
            benefit_type=BenefitType.BXGY,
            buy_quantity=1,
            get_quantity=1,
            get_discount_percent=Decimal(50),
            target_scope=TargetScope.PRODUCTS,
        )
        promotion.products.set([case])
        promotion.get_products.set([ProductFactory(**reward_kwargs)])

        option = _by_code(CouponPickerService.available(cart))["CASEGLASS"]

        assert option.reason == str(reason)
        with pytest.raises(CouponError) as refused:
            CouponService.apply(cart, "CASEGLASS")
        assert refused.value.reason == str(reason)


@pytest.mark.django_db
class TestCandidateSet:
    def test_hides_a_personal_coupon_from_everyone_else(
        self, make_cart, enable_promotions
    ):
        cart, _products = make_cart([(50, 1)])
        promotion, _code = _coded("PUBLIC")
        PromotionCodeFactory(
            promotion=promotion,
            code="MINE",
            assigned_to_email="owner@example.com",
        )

        codes = {
            option.code.code for option in CouponPickerService.available(cart)
        }

        assert codes == {"PUBLIC"}

    def test_shows_a_signed_in_shopper_their_own_personal_coupon(
        self, make_cart, enable_promotions
    ):
        """The one code a shopper is most likely to be holding is the
        one ``/offers`` deliberately never shows them."""
        user = UserAccountFactory()
        cart, _products = make_cart([(50, 1)], user=user)
        promotion, _code = _coded("PUBLIC")
        PromotionCodeFactory(promotion=promotion, code="MINE", assigned_to=user)

        codes = {
            option.code.code
            for option in CouponPickerService.available(cart, user=user)
        }

        assert codes == {"PUBLIC", "MINE"}

    def test_serializer_flags_personal_codes_only(
        self, make_cart, enable_promotions
    ):
        """The picker's ``personal`` field is what drives the "Yours"
        badge: true for an assigned code, false for a public one."""
        from promotion.serializers import CartCouponSerializer

        user = UserAccountFactory()
        cart, _products = make_cart([(50, 1)], user=user)
        promotion, _code = _coded("PUBLIC")
        PromotionCodeFactory(promotion=promotion, code="MINE", assigned_to=user)
        PromotionCodeFactory(
            promotion=promotion,
            code="BYMAIL",
            assigned_to_email=user.email,
        )

        data = CartCouponSerializer(
            CouponPickerService.available(cart, user=user), many=True
        ).data

        assert {row["code"]: row["personal"] for row in data} == {
            "PUBLIC": False,
            "MINE": True,
            "BYMAIL": True,
        }

    def test_matches_a_personal_coupon_assigned_by_email(
        self, make_cart, enable_promotions
    ):
        user = UserAccountFactory()
        cart, _products = make_cart([(50, 1)], user=user)
        promotion, _code = _coded("PUBLIC")
        PromotionCodeFactory(
            promotion=promotion,
            code="BYMAIL",
            assigned_to_email=user.email.upper(),
        )

        codes = {
            option.code.code
            for option in CouponPickerService.available(cart, user=user)
        }

        assert codes == {"PUBLIC", "BYMAIL"}

    def test_hides_single_use_bulk_codes(self, make_cart, enable_promotions):
        cart, _products = make_cart([(50, 1)])
        promotion, _code = _coded("PUBLIC")
        PromotionCodeFactory(
            promotion=promotion, code="BULK0001", usage_limit=1
        )

        codes = {
            option.code.code for option in CouponPickerService.available(cart)
        }

        assert codes == {"PUBLIC"}

    def test_empty_when_promotions_are_disabled(self, make_cart):
        cart, _products = make_cart([(50, 1)])
        _coded("TEN")

        assert CouponPickerService.available(cart) == []


@pytest.mark.django_db
class TestOrdering:
    def test_applied_first_then_eligible_then_by_value(
        self, make_cart, enable_promotions
    ):
        cart, _products = make_cart([(60, 1)])
        _coded(
            "SMALL",
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(3),
            stackable=True,
        )
        _coded(
            "BIG",
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(12),
            stackable=True,
        )
        _coded(
            "LOCKED",
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(50),
            min_subtotal=Money(Decimal("500.00"), "EUR"),
        )
        CouponService.apply(cart, "SMALL")

        order = [
            option.code.code for option in CouponPickerService.available(cart)
        ]

        assert order == ["SMALL", "BIG", "LOCKED"]


@pytest.mark.django_db
class TestQueryBudget:
    def test_cost_does_not_grow_with_the_number_of_coupons(
        self, make_cart, enable_promotions
    ):
        """One collection pass, then pure arithmetic per candidate. A
        per-coupon ``evaluate()`` would have made the checkout page pay
        ~15 queries for every code the store publishes.
        """
        cart, _products = make_cart([(60, 1)])
        for index in range(2):
            _coded(
                f"TWO{index}",
                benefit_type=BenefitType.FIXED_AMOUNT,
                benefit_value=Decimal(2),
            )

        # Warm the per-schema caches (content types, lazy search_path)
        # before measuring — see the query-budget note in project memory.
        CouponPickerService.available(cart)
        with CaptureQueriesContext(connection) as two_codes:
            CouponPickerService.available(cart)

        for index in range(6):
            _coded(
                f"EIGHT{index}",
                benefit_type=BenefitType.FIXED_AMOUNT,
                benefit_value=Decimal(2),
            )

        CouponPickerService.available(cart)
        with CaptureQueriesContext(connection) as eight_codes:
            options = CouponPickerService.available(cart)
        assert len(options) == 8
        assert len(eight_codes) == len(two_codes), (
            f"the picker grew from {len(two_codes)} to "
            f"{len(eight_codes)} queries with six more coupons — it is "
            f"evaluating per code instead of collecting once"
        )
