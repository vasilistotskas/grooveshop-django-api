"""``PromotionEngine``: the entry point every surface evaluates a cart
with, and the classification the coupon picker shares."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from djmoney.money import Money

from core.store_settings import store_setting
from promotion.engine.benefits import (
    amount_for,
    benefit_amount,
    gift_entitlement,
)
from promotion.engine.eligibility import (
    automatic_candidates,
    check_eligibility,
    collect_candidates,
)
from promotion.engine.results import (
    NEW_LINE_PK,
    AppliedPromotion,
    CartDiscountResult,
    NearMissEntry,
    SimulatedLine,
)
from promotion.engine.stacking import clamp, resolve_stacking
from promotion.engine.teasers import (
    bxgy_shortfall,
    items_after_teaser,
    minimum_near_miss,
    worthless_code_reason,
)
from promotion.enum import BenefitType, CouponRejectionReason
from promotion.models import Promotion, PromotionCode

if TYPE_CHECKING:
    from cart.models import CartItem


class PromotionEngine:
    """Stateless evaluation of promotions against a cart."""

    @classmethod
    def is_enabled(cls) -> bool:
        """Plan AND merchant toggle — both must hold.

        The coupon endpoints are plan-gated by DRF permissions, but
        AUTOMATIC promotions never touch them: they are collected
        during order create, which runs with no permission classes.
        See tenant.membership.tenant_plan_allows.
        """
        from tenant.membership import tenant_plan_allows

        if not tenant_plan_allows("promotions_enabled"):
            return False
        return bool(store_setting("PROMOTIONS_ENABLED"))

    @classmethod
    def evaluate(
        cls,
        cart,
        *,
        user=None,
        email: str = "",
        lock: bool = False,
        items: Sequence[CartItem] | None = None,
    ) -> CartDiscountResult:
        """Compute the discount the cart is entitled to right now.

        ``lock=True`` takes ``select_for_update`` on the candidate
        Promotion rows — use it ONLY inside the order-create
        transaction so usage-limit enforcement is race-free. The
        preview surfaces (cart read, the checkout quote) evaluate
        without locks; order creation recomputes with the lock held
        and is the source of truth.

        ``items`` are the cart's lines as the caller already read them:
        the checkout quote prices the lines it evaluated, from one read.
        Without them the engine reads the lines itself.
        """
        result = CartDiscountResult()
        if not cls.is_enabled():
            return result

        # Wholesale carts don't stack retail promotions unless the
        # merchant explicitly opts in (B2B_ALLOW_PROMOTIONS) — a
        # negotiated B2B price plus a retail promo double-discounts
        # silently. One gate here covers every caller (cart preview,
        # payment intent, order create), because pricing always binds
        # before evaluation on those paths. Attached codes surface as
        # COMBINATION_DISALLOWED (non-blocking, ACP vocabulary): a new
        # apply_coupon gets a clear refusal instead of silently
        # attaching a dead code, while order create proceeds without a
        # discount — exactly what the preview showed.
        from b2b.services import (
            B2BPricingService,
            B2BService,
        )

        if (
            B2BPricingService.cart_pricing_active(cart)
            and not B2BService.promotions_allowed()
        ):
            result.codes = list(
                cart.applied_codes.values_list("code__code", flat=True)
            )
            result.rejected.extend(
                (code, str(CouponRejectionReason.COMBINATION_DISALLOWED))
                for code in result.codes
            )
            return result

        cart_items = (
            list(items)
            if items is not None
            else list(cart.items.select_related("product"))
        )
        if not cart_items:
            return result

        currency = settings.DEFAULT_CURRENCY
        items_total = sum(
            (item.total_price.amount for item in cart_items), Decimal(0)
        )

        candidates, rejected, result.codes = collect_candidates(cart, lock=lock)
        result.rejected.extend(rejected)

        monetary, applicable_free_shipping = cls.classify(
            candidates,
            cart_items=cart_items,
            items_total=items_total,
            currency=currency,
            user=user,
            email=email,
            result=result,
        )

        chosen = resolve_stacking(monetary, result)
        chosen = clamp(chosen, cart_items, currency)
        # A code the clamp left with nothing (earlier promotions already
        # took the items total) moves no money: refuse it like a
        # stacking loser instead of accepting it for nothing.
        result.rejected.extend(
            (entry.code.code, str(CouponRejectionReason.COMBINATION_DISALLOWED))
            for entry in chosen
            if entry.code is not None and entry.amount.amount <= 0
        )

        result.applied = [e for e in chosen if e.amount.amount > 0]
        result.free_shipping = applicable_free_shipping
        result.near_miss = [
            entry
            for entry in result.near_miss
            if cls._teaser_holds(
                entry,
                candidates,
                cart_items=cart_items,
                currency=currency,
                user=user,
                email=email,
            )
        ]
        return result

    @classmethod
    def single_unit_discount(
        cls,
        promotions: list[Promotion],
        product,
        unit_price: Money,
    ) -> Decimal:
        """What ``promotions`` take off one unit of ``product`` bought alone.

        Runs the cart's own benefit, stacking and allocation rules
        (``clamp`` / ``allocate_capped``, the same ones the cart is
        charged with) over a one-line cart, so a listing price and the cart cannot disagree.
        The caller passes only promotions that need nothing from the
        basket (``promotion.offers.price_reducing_promotions``).
        """
        items = [SimulatedLine(NEW_LINE_PK, product, 1, unit_price)]
        currency = settings.DEFAULT_CURRENCY
        entries = []
        for promotion in promotions:
            amount = benefit_amount(
                promotion, items, unit_price.amount, currency
            )
            if amount.amount > 0:
                entries.append(AppliedPromotion(promotion, None, amount))
        chosen = clamp(
            resolve_stacking(entries, CartDiscountResult()),
            items,
            currency,
        )
        return sum((entry.amount.amount for entry in chosen), Decimal(0))

    @classmethod
    def evaluate_single_unit(cls, product) -> CartDiscountResult:
        """What a cart holding one ``product`` and nothing else gets.

        For a shopper not known yet and no code applied: the automatic
        promotions only, through the classification, stacking and clamp
        :meth:`evaluate` charges a cart with, over a one-line cart priced
        as a cart line is (``product.final_price``). The discount, the
        free shipping and the gifts it grants are the ones checkout
        would grant that cart, so a product page can quote that cart's
        shipping. ``first_order_only`` promotions are left out, as for
        any anonymous cart (``check_eligibility``).
        """
        result = CartDiscountResult()
        if not cls.is_enabled():
            return result
        line = SimulatedLine(NEW_LINE_PK, product, 1, product.final_price)
        items = [line]
        currency = settings.DEFAULT_CURRENCY
        monetary, free_shipping = cls.classify(
            automatic_candidates(),
            cart_items=items,
            items_total=line.total_price.amount,
            currency=currency,
            user=None,
            email="",
            result=result,
        )
        chosen = clamp(resolve_stacking(monetary, result), items, currency)
        result.applied = [e for e in chosen if e.amount.amount > 0]
        result.free_shipping = free_shipping
        return result

    @classmethod
    def _teaser_holds(
        cls,
        entry: NearMissEntry,
        candidates: list[tuple[Promotion, PromotionCode | None]],
        *,
        cart_items,
        currency: str,
        user,
        email: str,
    ) -> bool:
        """Would doing what the teaser says get its promotion granted?

        A teaser is a promise, so it is checked against the engine
        itself: the cart is rebuilt as it would be once the shopper adds
        what the teaser asks for, and the full classification, stacking
        and clamp run over it. Only a teaser whose promotion survives is
        shown. That rules out every way the promise could break — a
        stackable promotion a better non-stackable one would beat (demo
        store, 2026-10-10: "-5% from 3 items" teased on two cables, where
        the third cable unlocks a "2+1" worth more), a free gift that is
        out of stock, a reward the clamp would erase.
        """
        future_items = items_after_teaser(entry, cart_items)
        if future_items is None:
            return False
        future_total = sum(
            (item.total_price.amount for item in future_items), Decimal(0)
        )
        scratch = CartDiscountResult()
        monetary, _free_shipping = cls.classify(
            candidates,
            cart_items=future_items,
            items_total=future_total,
            currency=currency,
            user=user,
            email=email,
            result=scratch,
        )
        chosen = clamp(
            resolve_stacking(monetary, scratch), future_items, currency
        )
        granted = {e.promotion.pk for e in chosen if e.amount.amount > 0}
        granted |= {promotion.pk for promotion, _code in scratch.non_monetary}
        return entry.promotion.pk in granted

    @classmethod
    def classify(
        cls,
        candidates: list[tuple[Promotion, PromotionCode | None]],
        *,
        cart_items,
        items_total: Decimal,
        currency: str,
        user,
        email: str,
        result: CartDiscountResult,
    ) -> tuple[list[AppliedPromotion], bool]:
        """Eligibility, then benefit amount, for a candidate set.

        Fills ``result`` with the rejections, near-miss teasers, gift
        entitlements and non-monetary redemption rows it discovers, and
        returns the PRE-STACKING monetary entries plus whether shipping
        is waived. Split out of ``evaluate`` because the checkout coupon
        picker (``CouponPickerService.available``) has to run exactly this
        classification over a different candidate set — an unattached
        coupon each time — and a second copy of the rules would drift
        from the one the cart is actually charged with.
        """
        monetary: list[AppliedPromotion] = []
        eligible: list[tuple[Promotion, PromotionCode | None]] = []
        for promotion, code in candidates:
            reason = check_eligibility(
                promotion,
                code,
                cart_items=cart_items,
                items_total=items_total,
                currency=currency,
                user=user,
                email=email,
            )
            if reason is not None:
                if code is not None:
                    result.rejected.append((code.code, str(reason)))
                elif reason == str(CouponRejectionReason.MINIMUM_NOT_MET):
                    entry = minimum_near_miss(
                        promotion, cart_items, items_total
                    )
                    if entry is not None:
                        result.near_miss.append(entry)
                continue
            eligible.append((promotion, code))

        free_shipping: list[tuple[Promotion, PromotionCode | None]] = []
        for promotion, code in eligible:
            if promotion.benefit_type == BenefitType.FREE_SHIPPING:
                # Free shipping has no amount to conflict over — it
                # always combines (``stackable`` is ignored, see the
                # model help_text). Settled after the loop, once it is
                # known whether an automatic promotion already waives it.
                free_shipping.append((promotion, code))
                continue
            if promotion.benefit_type == BenefitType.FREE_GIFT:
                entitlement = gift_entitlement(promotion)
                if entitlement is not None:
                    result.gift_items.append(entitlement)
                    result.non_monetary.append((promotion, code))
                elif code is not None:
                    # No gift it could hand over, so applying the code
                    # would change nothing.
                    result.rejected.append(
                        (
                            code.code,
                            str(CouponRejectionReason.GIFT_UNAVAILABLE),
                        )
                    )
                continue
            amount = amount_for(promotion, cart_items, items_total, currency)
            if amount.amount <= 0:
                # Worth nothing on this cart yet. An automatic buy-X-
                # get-Y short of its first group becomes an "add N more"
                # teaser; a code is refused, never accepted for nothing.
                shortfall = (
                    bxgy_shortfall(promotion, cart_items)
                    if promotion.benefit_type == BenefitType.BXGY
                    else None
                )
                if code is None:
                    if shortfall is not None:
                        result.near_miss.append(shortfall)
                    continue
                result.rejected.append(
                    (code.code, worthless_code_reason(shortfall))
                )
                continue
            monetary.append(AppliedPromotion(promotion, code, amount))

        # A free-shipping code on a cart an automatic promotion already
        # ships free would change nothing, so it is refused like a
        # stacking loser rather than "applied" with no effect.
        waived = any(code is None for _promotion, code in free_shipping)
        for promotion, code in free_shipping:
            if code is not None and waived:
                result.rejected.append(
                    (
                        code.code,
                        str(CouponRejectionReason.COMBINATION_DISALLOWED),
                    )
                )
                continue
            result.non_monetary.append((promotion, code))
        applicable_free_shipping = bool(free_shipping)

        return monetary, applicable_free_shipping
