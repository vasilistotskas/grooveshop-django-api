"""CouponService cart attach/detach semantics."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone
from djmoney.money import Money

from promotion.enum import CouponRejectionReason
from promotion.factories import PromotionCodeFactory, PromotionFactory
from promotion.models import CartPromotionCode
from promotion.services import CouponError, CouponService

pytestmark = pytest.mark.django_db


class TestApply:
    def test_apply_attaches_and_returns_result(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        code = PromotionCodeFactory(
            promotion=PromotionFactory(benefit_value=Decimal(10))
        )

        result = CouponService.apply(cart, code.code.lower())

        assert result.discount_total.amount == Decimal("10.00")
        assert CartPromotionCode.objects.filter(cart=cart, code=code).exists()

    def test_apply_unknown_code_raises_invalid(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])

        with pytest.raises(CouponError) as excinfo:
            CouponService.apply(cart, "NOPE123")

        assert excinfo.value.reason == str(CouponRejectionReason.INVALID)

    def test_apply_scheduled_code_raises_not_started(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        code = PromotionCodeFactory(
            promotion=PromotionFactory(
                starts_at=timezone.now() + timezone.timedelta(days=2)
            )
        )

        with pytest.raises(CouponError) as excinfo:
            CouponService.apply(cart, code.code)

        assert excinfo.value.reason == str(CouponRejectionReason.NOT_STARTED)

    def test_apply_ended_code_raises_expired(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        code = PromotionCodeFactory(
            promotion=PromotionFactory(
                ends_at=timezone.now() - timezone.timedelta(days=1)
            )
        )

        with pytest.raises(CouponError) as excinfo:
            CouponService.apply(cart, code.code)

        assert excinfo.value.reason == str(CouponRejectionReason.EXPIRED)

    def test_apply_ineligible_code_detaches_and_reports_reason(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(30, 1)])
        code = PromotionCodeFactory(
            promotion=PromotionFactory(min_subtotal=Money(Decimal(50), "EUR"))
        )

        with pytest.raises(CouponError) as excinfo:
            CouponService.apply(cart, code.code)

        assert excinfo.value.reason == str(
            CouponRejectionReason.MINIMUM_NOT_MET
        )
        assert not CartPromotionCode.objects.filter(cart=cart).exists()

    def test_apply_replaces_previous_code(self, enable_promotions, make_cart):
        cart, _ = make_cart([(100, 1)])
        first = PromotionCodeFactory(promotion=PromotionFactory())
        second = PromotionCodeFactory(promotion=PromotionFactory())

        CouponService.apply(cart, first.code)
        CouponService.apply(cart, second.code)

        attached = list(
            CartPromotionCode.objects.filter(cart=cart).values_list(
                "code__code", flat=True
            )
        )
        assert attached == [second.code]

    def test_apply_disabled_feature_raises_invalid(self, make_cart):
        cart, _ = make_cart([(100, 1)])
        code = PromotionCodeFactory(promotion=PromotionFactory())

        with pytest.raises(CouponError):
            CouponService.apply(cart, code.code)


class TestRemove:
    def test_remove_detaches(self, enable_promotions, make_cart):
        cart, _ = make_cart([(100, 1)])
        code = PromotionCodeFactory(promotion=PromotionFactory())
        CouponService.apply(cart, code.code)

        CouponService.remove(cart)

        assert not CartPromotionCode.objects.filter(cart=cart).exists()
