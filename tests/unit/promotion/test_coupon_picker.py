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
from tests.utils import count_queries
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

    def test_a_code_worth_nothing_on_this_cart_still_applies(
        self, make_cart, enable_promotions
    ):
        """A product-scoped coupon whose products are not in the cart
        computes to zero, so ``_classify`` drops it before stacking and
        no rejection is raised — apply() accepts it. Enabled, with the
        amount telling the truth."""
        cart, _products = make_cart([(100, 1)])
        promotion, _code = _coded(
            "ELSEWHERE",
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal(20),
            target_scope=TargetScope.PRODUCTS,
        )
        promotion.products.set([ProductFactory()])

        option = _by_code(CouponPickerService.available(cart))["ELSEWHERE"]

        assert option.eligible is True
        assert option.amount == Money(Decimal(0), "EUR")
        CouponService.apply(cart, "ELSEWHERE")

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

    def test_free_shipping_claims_nothing_when_an_automatic_already_waives(
        self, make_cart, enable_promotions
    ):
        cart, _products = make_cart([(50, 1)])
        _automatic(benefit_type=BenefitType.FREE_SHIPPING, stackable=True)
        _coded("SHIPFREE", benefit_type=BenefitType.FREE_SHIPPING)

        option = _by_code(CouponPickerService.available(cart))["SHIPFREE"]

        assert option.eligible is True
        assert option.free_shipping is False


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
        with count_queries() as two_codes:
            CouponPickerService.available(cart)

        for index in range(6):
            _coded(
                f"EIGHT{index}",
                benefit_type=BenefitType.FIXED_AMOUNT,
                benefit_value=Decimal(2),
            )

        CouponPickerService.available(cart)
        with count_queries() as eight_codes:
            options = CouponPickerService.available(cart)
        assert len(options) == 8
        assert eight_codes.count == two_codes.count, (
            f"the picker grew from {two_codes.count} to "
            f"{eight_codes.count} queries with six more coupons — it is "
            f"evaluating per code instead of collecting once"
        )
