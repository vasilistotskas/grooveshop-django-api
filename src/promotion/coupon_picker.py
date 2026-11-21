"""The coupons the checkout picker offers, each with the verdict applying
it would get."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from djmoney.money import Money

from promotion.engine.benefits import amount_for, gift_entitlement
from promotion.engine.eligibility import (
    check_eligibility,
    collect_candidates,
    dead_window_reason,
)
from promotion.engine.evaluation import PromotionEngine
from promotion.engine.results import AppliedPromotion, CartDiscountResult
from promotion.engine.scope import attach_category_scopes
from promotion.engine.stacking import clamp, resolve_stacking
from promotion.engine.teasers import bxgy_shortfall, worthless_code_reason
from promotion.enum import BenefitType, CouponRejectionReason, PromotionTrigger
from promotion.managers.promotion import publishable_code_q
from promotion.models import CartPromotionCode, Promotion, PromotionCode


@dataclass
class CouponOption:
    """One coupon the checkout picker may offer, with its verdict."""

    promotion: Promotion
    code: PromotionCode
    # ``reason is None`` IS the verdict: it means ``CouponService.apply``
    # would accept the code, so a disabled row in the picker and a
    # refusal at apply time can never disagree.
    reason: str | None
    # What applying it RIGHT NOW would take off, measured against the
    # cart's automatic promotions alone — because applying a coupon
    # replaces whatever code is attached (v1 is one coupon per cart).
    # Zero for an eligible coupon only when its benefit is not money (free
    # shipping, a gift): a monetary code worth nothing is refused.
    amount: Money
    free_shipping: bool
    applied: bool

    @property
    def eligible(self) -> bool:
        return self.reason is None


class CouponPickerService:
    """The coupons a shopper may be shown at checkout, pre-judged.

    Typing a code is a guessing game the store already knows the answer
    to. This lists the codes the store publishes (plus, for a signed-in
    shopper, the personal ones assigned to them), each carrying the
    verdict ``CouponService.apply`` would return for the cart as it
    stands.

    Every verdict comes from ``PromotionEngine`` itself — one
    ``collect_candidates`` pass for the automatic baseline, then the
    pure stacking/clamping maths re-run per candidate — so the picker
    costs a constant number of queries rather than one evaluation per
    coupon.
    """

    # A store running more advertisable coupons than this is not
    # running a picker, it is running a catalogue; the cap keeps the
    # checkout request bounded. Ordered most-valuable-first before the
    # slice, so the ones that are cut are the ones worth least.
    MAX_COUPONS = 24

    @classmethod
    def available(
        cls, cart, *, user=None, email: str = ""
    ) -> list[CouponOption]:
        if not PromotionEngine.is_enabled():
            return []

        # Same suppression the engine applies to wholesale carts: when
        # retail promotions don't stack on B2B prices the backend
        # refuses every code, so offering a picker would be a list of
        # buttons that all fail.
        from b2b.services import B2BPricingService, B2BService

        if (
            B2BPricingService.cart_pricing_active(cart)
            and not B2BService.promotions_allowed()
        ):
            return []

        codes = cls._candidate_codes(user=user)
        if not codes:
            return []

        cart_items = list(cart.items.select_related("product"))
        currency = settings.DEFAULT_CURRENCY
        items_total = sum(
            (item.total_price.amount for item in cart_items), Decimal(0)
        )
        attached = set(
            CartPromotionCode.objects.filter(cart=cart).values_list(
                "code__code", flat=True
            )
        )

        baseline_monetary, baseline_shipping = cls._automatic_baseline(
            cart,
            cart_items=cart_items,
            items_total=items_total,
            currency=currency,
            user=user,
            email=email,
        )
        # Only the total matters for the baseline: the automatic set
        # has no coupon codes in it, so the losers it throws out are
        # never rows this picker offers.
        baseline_total, _baseline_losers = cls._stacked_total(
            baseline_monetary, cart_items, currency
        )

        attach_category_scopes([code.promotion for code in codes])
        options = [
            cls._option(
                code,
                cart_items=cart_items,
                items_total=items_total,
                currency=currency,
                user=user,
                email=email,
                baseline_monetary=baseline_monetary,
                baseline_total=baseline_total,
                baseline_shipping=baseline_shipping,
                attached=attached,
            )
            for code in codes
        ]
        # Usable first, then by what they are worth. ``applied`` floats
        # to the very top so the shopper can always find (and remove)
        # the code the cart is already carrying.
        options.sort(
            key=lambda option: (
                not option.applied,
                not option.eligible,
                -option.amount.amount,
                not option.free_shipping,
                option.promotion.priority,
                option.promotion.id,
            )
        )
        return options[: cls.MAX_COUPONS]

    @classmethod
    def _candidate_codes(cls, *, user) -> list[PromotionCode]:
        """Publicly advertisable codes, plus the shopper's own.

        A personal coupon is invisible on ``/offers`` by design, so
        without the second clause the one code a signed-in shopper is
        most likely to have been sent is the one the picker would never
        show them. It is scoped to the authenticated identity only —
        a guest cart carries no verified identity to match against.

        The promotions are loaded in a SECOND query and grafted onto
        the codes rather than pulled in with ``select_related`` or a
        forward ``Prefetch``: neither carries the scope relations
        ``matching_items`` then walks, and the version that looked
        like it did cost five queries per coupon — measured at 46
        queries for eight codes, which is the whole reason this
        service exists instead of an ``evaluate()`` per code.
        """
        listable = (
            Promotion.objects.live()
            .publicly_listable()
            .filter(trigger=PromotionTrigger.CODE)
            .values_list("id", flat=True)
        )
        public = Q(promotion_id__in=list(listable)) & publishable_code_q()

        owned = Q(pk__in=[])
        if user is not None and getattr(user, "is_authenticated", False):
            owned = Q(assigned_to=user)
            if user.email:
                owned |= Q(assigned_to_email__iexact=user.email)

        codes = list(
            PromotionCode.objects.filter(
                Q(is_active=True)
                & Q(promotion__trigger=PromotionTrigger.CODE)
                & (public | owned)
            ).order_by("promotion_id", "created_at")
        )
        if not codes:
            return []

        promotions = {
            promotion.pk: promotion
            for promotion in Promotion.objects.filter(
                pk__in={code.promotion_id for code in codes}
            )
            .for_list()
            .with_scope_relations()
        }
        for code in codes:
            code.promotion = promotions[code.promotion_id]
        codes.sort(key=lambda code: (code.promotion.priority, code.pk))
        return codes

    @classmethod
    def _automatic_baseline(
        cls,
        cart,
        *,
        cart_items,
        items_total: Decimal,
        currency: str,
        user,
        email: str,
    ) -> tuple[list[AppliedPromotion], bool]:
        """What the cart already earns with no coupon attached.

        Applying a coupon REPLACES the attached one (v1 policy is one
        code per cart), so the honest baseline for "what would this
        coupon add" excludes every attached code — including, when the
        picker re-reads the coupon that is already on, itself.
        """
        candidates, _rejected = collect_candidates(cart, lock=False)
        automatic = [
            (promotion, code) for promotion, code in candidates if code is None
        ]
        return PromotionEngine.classify(
            automatic,
            cart_items=cart_items,
            items_total=items_total,
            currency=currency,
            user=user,
            email=email,
            result=CartDiscountResult(),
        )

    @staticmethod
    def _stacked_total(
        monetary: list[AppliedPromotion],
        cart_items,
        currency: str,
    ) -> tuple[Decimal, set[str]]:
        """What the engine would charge for this entry set, and which
        codes the stacking resolution threw out.

        Both halves matter. The total is what the cart would actually
        show; the losing codes are what ``CouponService.apply`` REFUSES
        — it raises on any rejection the evaluation produces,
        COMBINATION_DISALLOWED included — so a picker that reported
        only the total would offer an enabled button for a code the
        very next request rejects with a 400.
        """
        scratch = CartDiscountResult()
        chosen = resolve_stacking(monetary, scratch)
        clamped = clamp(chosen, cart_items, currency)
        total = sum((entry.amount.amount for entry in clamped), Decimal(0))
        losers = {
            rejected_code
            for rejected_code, reason in scratch.rejected
            if reason == str(CouponRejectionReason.COMBINATION_DISALLOWED)
        }
        # ``evaluate`` refuses a code the clamp left with nothing too.
        losers |= {
            entry.code.code
            for entry in clamped
            if entry.code is not None and entry.amount.amount <= 0
        }
        return total, losers

    @classmethod
    def _option(
        cls,
        code: PromotionCode,
        *,
        cart_items,
        items_total: Decimal,
        currency: str,
        user,
        email: str,
        baseline_monetary: list[AppliedPromotion],
        baseline_total: Decimal,
        baseline_shipping: bool,
        attached: set[str],
    ) -> CouponOption:
        promotion = code.promotion
        zero = Money(Decimal(0), currency)
        applied = code.code in attached

        if not promotion.is_live:
            return CouponOption(
                promotion=promotion,
                code=code,
                reason=str(dead_window_reason(promotion)),
                amount=zero,
                free_shipping=False,
                applied=applied,
            )

        reason = check_eligibility(
            promotion,
            code,
            cart_items=cart_items,
            items_total=items_total,
            currency=currency,
            user=user,
            email=email,
        )
        if reason is not None or not cart_items:
            return CouponOption(
                promotion=promotion,
                code=code,
                reason=reason,
                amount=zero,
                free_shipping=False,
                applied=applied,
            )

        if promotion.benefit_type == BenefitType.FREE_SHIPPING:
            # Same verdict as ``classify``: with an automatic offer
            # already waiving shipping the code would change nothing.
            return CouponOption(
                promotion=promotion,
                code=code,
                reason=str(CouponRejectionReason.COMBINATION_DISALLOWED)
                if baseline_shipping
                else None,
                amount=zero,
                free_shipping=not baseline_shipping,
                applied=applied,
            )
        if promotion.benefit_type == BenefitType.FREE_GIFT:
            entitlement = gift_entitlement(promotion)
            return CouponOption(
                promotion=promotion,
                code=code,
                reason=None
                if entitlement is not None
                else str(CouponRejectionReason.GIFT_UNAVAILABLE),
                amount=zero,
                free_shipping=False,
                applied=applied,
            )

        amount = amount_for(promotion, cart_items, items_total, currency)
        if amount.amount <= 0:
            # Same verdict as ``classify``, so the picker never offers
            # a code ``apply`` refuses.
            shortfall = (
                bxgy_shortfall(promotion, cart_items)
                if promotion.benefit_type == BenefitType.BXGY
                else None
            )
            return CouponOption(
                promotion=promotion,
                code=code,
                reason=worthless_code_reason(shortfall),
                amount=zero,
                free_shipping=False,
                applied=applied,
            )

        entry = AppliedPromotion(promotion, code, amount)
        total_with, losers = cls._stacked_total(
            [*baseline_monetary, entry], cart_items, currency
        )
        if code.code in losers:
            # A non-stackable coupon the automatic offers already beat.
            # ``CouponService.apply`` raises on this rejection, so the
            # row has to be disabled and say why — an enabled button
            # that answers 400 is the one failure a pre-judged picker
            # exists to prevent.
            return CouponOption(
                promotion=promotion,
                code=code,
                reason=str(CouponRejectionReason.COMBINATION_DISALLOWED),
                amount=zero,
                free_shipping=False,
                applied=applied,
            )

        return CouponOption(
            promotion=promotion,
            code=code,
            reason=None,
            amount=Money(
                max(Decimal(0), total_with - baseline_total), currency
            ),
            free_shipping=False,
            applied=applied,
        )
