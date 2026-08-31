"""Promotion evaluation engine and coupon cart operations.

One engine serves every surface that needs to know "what discount does
this cart get": the cart serializer (preview), the pre-order
payment-intent amount, the order-create verification guard, and the
order-create application itself. All of them MUST agree to the cent —
``PaymentAmountMismatchError`` is the alarm that fires when they don't.

Value flow order (fixed): product markdown (already inside
``Product.final_price``) → promotions (this engine) → loyalty
redemption → gift-card payment. The engine never re-derives product
markdown; it discounts the already-marked-down cart line totals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from djmoney.money import Money
from extra_settings.models import Setting

from promotion.enum import (
    BenefitType,
    CouponRejectionReason,
    PromotionTrigger,
    TargetScope,
)
from promotion.models import (
    CartPromotionCode,
    Promotion,
    PromotionCode,
    PromotionRedemption,
)

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")


class CouponError(ValidationError):
    """A coupon code was refused; ``reason`` is machine-readable."""

    def __init__(self, reason: str):
        self.reason = str(reason)
        label = dict(CouponRejectionReason.choices).get(
            reason, CouponRejectionReason.INVALID.label
        )
        super().__init__(str(label))


@dataclass
class AppliedPromotion:
    promotion: Promotion
    code: PromotionCode | None
    amount: Money


@dataclass
class GiftEntitlement:
    """A FREE_GIFT promotion's reward: ``quantity`` units of
    ``product`` added to the order at price 0."""

    promotion: Promotion
    product: object
    quantity: int


@dataclass
class NearMissEntry:
    """An automatic promotion the cart ALMOST qualifies for — blocked
    only by its minimum subtotal. Powers the storefront's "add X more
    to unlock" teaser (never emitted for an empty cart)."""

    promotion: Promotion
    remaining_amount: Decimal


@dataclass
class CartDiscountResult:
    applied: list[AppliedPromotion] = field(default_factory=list)
    free_shipping: bool = False
    gift_items: list[GiftEntitlement] = field(default_factory=list)
    near_miss: list[NearMissEntry] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    # FREE_SHIPPING / FREE_GIFT promotions that applied — recorded as
    # zero-amount redemption rows so their usage limits are enforced
    # exactly like monetary promotions.
    non_monetary: list[tuple[Promotion, PromotionCode | None]] = field(
        default_factory=list
    )

    @property
    def discount_total(self) -> Money:
        currency = settings.DEFAULT_CURRENCY
        total = sum(
            (entry.amount.amount for entry in self.applied), Decimal("0")
        )
        return Money(total, currency)

    @property
    def blocking_rejections(self) -> list[tuple[str, str]]:
        """Rejections that must abort order creation.

        A code losing the stacking comparison to a better automatic
        promotion (combination_disallowed) is not blocking — the
        shopper is charged LESS than the code alone would give, and
        the cart preview showed the same outcome.
        """
        return [
            (code, reason)
            for code, reason in self.rejected
            if reason != str(CouponRejectionReason.COMBINATION_DISALLOWED)
        ]


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


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
        from tenant.membership import tenant_plan_allows  # noqa: PLC0415

        if not tenant_plan_allows("promotions_enabled"):
            return False
        return bool(Setting.get("PROMOTIONS_ENABLED", default=False))

    @classmethod
    def evaluate(
        cls,
        cart,
        *,
        user=None,
        email: str = "",
        lock: bool = False,
    ) -> CartDiscountResult:
        """Compute the discount the cart is entitled to right now.

        ``lock=True`` takes ``select_for_update`` on the candidate
        Promotion rows — use it ONLY inside the order-create
        transaction so usage-limit enforcement is race-free. The
        preview surfaces (cart read, payment-intent create) evaluate
        without locks; the order-create verification recomputes with
        the lock held and is the source of truth.
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
        from b2b.services import (  # noqa: PLC0415
            B2BPricingService,
            B2BService,
        )

        if (
            B2BPricingService.cart_pricing_active(cart)
            and not B2BService.promotions_allowed()
        ):
            result.rejected.extend(
                (code, str(CouponRejectionReason.COMBINATION_DISALLOWED))
                for code in cart.applied_codes.values_list(
                    "code__code", flat=True
                )
            )
            return result

        cart_items = list(cart.items.select_related("product"))
        if not cart_items:
            return result

        currency = settings.DEFAULT_CURRENCY
        items_total = sum(
            (item.total_price.amount for item in cart_items), Decimal("0")
        )

        candidates, rejected = cls._collect_candidates(cart, lock=lock)
        result.rejected.extend(rejected)

        monetary: list[AppliedPromotion] = []
        applicable_free_shipping = False
        eligible: list[tuple[Promotion, PromotionCode | None]] = []
        for promotion, code in candidates:
            reason = cls._check_eligibility(
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
                elif (
                    reason == str(CouponRejectionReason.MINIMUM_NOT_MET)
                    and promotion.min_subtotal
                ):
                    # Near-miss teaser: an automatic promotion blocked
                    # ONLY by its minimum subtotal. The strictly-exceed
                    # distance never reports 0 for a cart resting
                    # exactly on the threshold (adveshop near-miss
                    # design note).
                    remaining = _quantize(
                        Decimal(str(promotion.min_subtotal.amount))
                        - items_total
                    )
                    if remaining > 0:
                        result.near_miss.append(
                            NearMissEntry(promotion, remaining)
                        )
                continue
            eligible.append((promotion, code))

        for promotion, code in eligible:
            if promotion.benefit_type == BenefitType.FREE_SHIPPING:
                # Free shipping has no amount to conflict over — it
                # always combines (``stackable`` is ignored, see the
                # model help_text).
                applicable_free_shipping = True
                result.non_monetary.append((promotion, code))
                continue
            if promotion.benefit_type == BenefitType.FREE_GIFT:
                entitlement = cls._gift_entitlement(promotion)
                if entitlement is not None:
                    result.gift_items.append(entitlement)
                    result.non_monetary.append((promotion, code))
                continue
            if promotion.benefit_type == BenefitType.BXGY:
                amount = cls._bxgy_amount(promotion, cart_items, currency)
            else:
                amount = cls._benefit_amount(
                    promotion, cart_items, items_total, currency
                )
            if amount.amount <= 0:
                continue
            monetary.append(AppliedPromotion(promotion, code, amount))

        chosen = cls._resolve_stacking(monetary, result)

        # Never discount below zero items value.
        running = Decimal("0")
        for entry in chosen:
            available = items_total - running
            if available <= 0:
                entry.amount = Money(Decimal("0"), currency)
                continue
            clamped = min(entry.amount.amount, available)
            entry.amount = Money(_quantize(clamped), currency)
            running += entry.amount.amount

        result.applied = [e for e in chosen if e.amount.amount > 0]
        result.free_shipping = applicable_free_shipping
        return result

    @classmethod
    def gift_weight_grams(cls, result: CartDiscountResult) -> int:
        """Extra parcel weight the entitled gift items add.

        Shipping quotes are weight-banded (ACS) — every surface that
        prices shipping (payment-intent, verification, creation) must
        include the gifts or the courier voucher upcharges later.
        """
        if not result.gift_items:
            return 0
        from shipping.utils import (  # noqa: PLC0415
            compute_total_weight_grams,
        )

        return compute_total_weight_grams(
            (gift.product, gift.quantity) for gift in result.gift_items
        )

    @classmethod
    def record(cls, order, result: CartDiscountResult) -> Money:
        """Persist redemption rows for an evaluated result.

        Must run inside the same transaction (and Promotion locks) as
        the ``evaluate(lock=True)`` call that produced ``result``.
        Returns the discount total to store on the order.
        """
        for entry in result.applied:
            PromotionRedemption.objects.create(
                promotion=entry.promotion,
                code=entry.code,
                order=order,
                user=order.user,
                email=order.email or "",
                amount=entry.amount,
            )
        # Zero-amount rows for FREE_SHIPPING / FREE_GIFT applications —
        # without them their usage limits would never count anything.
        for promotion, code in result.non_monetary:
            PromotionRedemption.objects.create(
                promotion=promotion,
                code=code,
                order=order,
                user=order.user,
                email=order.email or "",
                amount=Money(0, settings.DEFAULT_CURRENCY),
            )
        if result.applied:
            order.metadata["promotions"] = [
                {
                    "promotion_id": entry.promotion.id,
                    "name": entry.promotion.safe_translation_getter(
                        "name", any_language=True
                    )
                    or "",
                    "code": entry.code.code if entry.code else None,
                    "amount": str(entry.amount.amount),
                }
                for entry in result.applied
            ]
        if result.gift_items:
            order.metadata["promotion_gifts"] = [
                {
                    "promotion_id": gift.promotion.id,
                    "product_id": gift.product.id,
                    "quantity": gift.quantity,
                }
                for gift in result.gift_items
            ]
        if result.free_shipping:
            order.metadata["promotion_free_shipping"] = True
        return result.discount_total

    @classmethod
    def _collect_candidates(
        cls, cart, *, lock: bool
    ) -> tuple[
        list[tuple[Promotion, PromotionCode | None]],
        list[tuple[str, str]],
    ]:
        rejected: list[tuple[str, str]] = []
        cart_codes = list(
            CartPromotionCode.objects.filter(cart=cart).select_related(
                "code", "code__promotion", "code__assigned_to"
            )
        )

        automatic_ids = list(
            Promotion.objects.live().automatic().values_list("id", flat=True)
        )
        code_promotion_ids = [
            cc.code.promotion_id for cc in cart_codes if cc.code.is_active
        ]

        promotions_qs = Promotion.objects.filter(
            pk__in={*automatic_ids, *code_promotion_ids}
        ).order_by("pk")
        if lock:
            promotions_qs = promotions_qs.select_for_update()
        promotions = {p.pk: p for p in promotions_qs}

        candidates: list[tuple[Promotion, PromotionCode | None]] = []
        for pk in automatic_ids:
            promotion = promotions.get(pk)
            if promotion is not None:
                candidates.append((promotion, None))

        for cart_code in cart_codes:
            code = cart_code.code
            promotion = promotions.get(code.promotion_id)
            if not code.is_active or promotion is None:
                rejected.append((code.code, str(CouponRejectionReason.INVALID)))
                continue
            if not promotion.is_live:
                rejected.append(
                    (code.code, str(cls._dead_window_reason(promotion)))
                )
                continue
            candidates.append((promotion, code))

        return candidates, rejected

    @staticmethod
    def _dead_window_reason(promotion: Promotion) -> CouponRejectionReason:
        """Why a non-live promotion is refused: scheduled vs over."""
        if (
            promotion.is_active
            and promotion.starts_at
            and promotion.starts_at > timezone.now()
        ):
            return CouponRejectionReason.NOT_STARTED
        return CouponRejectionReason.EXPIRED

    @classmethod
    def _check_eligibility(
        cls,
        promotion: Promotion,
        code: PromotionCode | None,
        *,
        cart_items,
        items_total: Decimal,
        currency: str,
        user,
        email: str,
    ) -> str | None:
        if (
            code is not None
            and (code.assigned_to_id or code.assigned_to_email)
            and not cls._is_code_owner(code, user, email)
        ):
            return str(CouponRejectionReason.USER_INELIGIBLE)

        if promotion.min_subtotal and items_total < Decimal(
            str(promotion.min_subtotal.amount)
        ):
            return str(CouponRejectionReason.MINIMUM_NOT_MET)

        if promotion.min_quantity:
            eligible_units = sum(
                item.quantity
                for item in cls._matching_items(promotion, cart_items)
            )
            if eligible_units < promotion.min_quantity:
                return str(CouponRejectionReason.MINIMUM_NOT_MET)

        if promotion.first_order_only:
            from order.models.order import Order  # noqa: PLC0415

            if user is not None and getattr(user, "is_authenticated", False):
                has_orders = Order.objects.filter(user=user).exists()
            elif email:
                has_orders = Order.objects.filter(email__iexact=email).exists()
            else:
                # Identity unknown (anonymous cart stage). An applied
                # CODE is accepted optimistically — checkout always
                # carries the email and delivers the final verdict
                # before anything is charged. AUTOMATIC promotions
                # stay conservative so the anonymous preview never
                # shows a discount checkout would take away.
                has_orders = code is None
            if has_orders:
                return str(CouponRejectionReason.USER_INELIGIBLE)

        redemptions = PromotionRedemption.objects.filter(promotion=promotion)
        if (
            promotion.usage_limit_total is not None
            and redemptions.count() >= promotion.usage_limit_total
        ):
            return str(CouponRejectionReason.USAGE_LIMIT_REACHED)

        if promotion.usage_limit_per_customer is not None:
            if user is not None and getattr(user, "is_authenticated", False):
                used = redemptions.filter(user=user).count()
            elif email:
                used = redemptions.filter(email__iexact=email).count()
            else:
                used = 0
            if used >= promotion.usage_limit_per_customer:
                return str(CouponRejectionReason.USAGE_LIMIT_REACHED)

        if (
            code is not None
            and code.usage_limit is not None
            and code.redemptions.count() >= code.usage_limit
        ):
            return str(CouponRejectionReason.USAGE_LIMIT_REACHED)

        return None

    @staticmethod
    def _is_code_owner(code: PromotionCode, user, email: str) -> bool:
        """Whether the acting customer is the assignee of a personal
        coupon. Unknown identity at the anonymous cart stage is
        optimistic — checkout always carries the email and delivers
        the final verdict before anything is charged."""
        authenticated = user is not None and getattr(
            user, "is_authenticated", False
        )
        identity_emails = set()
        if authenticated and user.email:
            identity_emails.add(user.email.lower())
        if email:
            identity_emails.add(email.lower())

        if not authenticated and not identity_emails:
            return True

        if authenticated and code.assigned_to_id == user.pk:
            return True

        allowed_emails = set()
        if code.assigned_to_email:
            allowed_emails.add(code.assigned_to_email.lower())
        if code.assigned_to is not None and code.assigned_to.email:
            allowed_emails.add(code.assigned_to.email.lower())
        return bool(identity_emails & allowed_emails)

    @classmethod
    def _benefit_amount(
        cls,
        promotion: Promotion,
        cart_items,
        items_total: Decimal,
        currency: str,
    ) -> Money:
        base = sum(
            (
                item.total_price.amount
                for item in cls._matching_items(promotion, cart_items)
            ),
            Decimal("0"),
        )
        if base <= 0:
            return Money(Decimal("0"), currency)

        if promotion.benefit_type == BenefitType.PERCENTAGE:
            amount = base * promotion.benefit_value / Decimal("100")
        else:  # FIXED_AMOUNT
            amount = min(promotion.benefit_value, base)

        if promotion.max_discount_amount:
            amount = min(
                amount, Decimal(str(promotion.max_discount_amount.amount))
            )
        return Money(_quantize(amount), currency)

    @classmethod
    def _expanded_category_ids(cls, categories_qs) -> set[int]:
        from product.models.category import ProductCategory  # noqa: PLC0415

        ids = list(categories_qs.values_list("id", flat=True))
        if not ids:
            return set()
        return set(
            ProductCategory.objects.filter(pk__in=ids)
            .get_descendants(include_self=True)
            .values_list("id", flat=True)
        )

    @classmethod
    def _matching_items(cls, promotion: Promotion, cart_items) -> list:
        """Cart items the promotion may count and discount.

        Scope first (ORDER = everything, PRODUCTS/CATEGORIES with MPTT
        descendants), then the exclusion set: excluded products,
        excluded categories (descendants included), and — when
        ``exclude_discounted_products`` — anything already carrying a
        product-level markdown. Exclusions shrink both the discount
        base AND condition counting (min_quantity), everywhere,
        consistently.
        """
        if promotion.target_scope == TargetScope.PRODUCTS:
            include_ids = set(promotion.products.values_list("id", flat=True))
            items = [
                item for item in cart_items if item.product_id in include_ids
            ]
        elif promotion.target_scope == TargetScope.CATEGORIES:
            include_categories = cls._expanded_category_ids(
                promotion.categories
            )
            items = [
                item
                for item in cart_items
                if item.product.category_id in include_categories
            ]
        else:
            items = list(cart_items)

        excluded_ids = set(
            promotion.excluded_products.values_list("id", flat=True)
        )
        if excluded_ids:
            items = [
                item for item in items if item.product_id not in excluded_ids
            ]

        excluded_categories = cls._expanded_category_ids(
            promotion.excluded_categories
        )
        if excluded_categories:
            items = [
                item
                for item in items
                if item.product.category_id not in excluded_categories
            ]

        if promotion.exclude_discounted_products:
            items = [
                item
                for item in items
                if not item.product.discount_percent
                or item.product.discount_percent <= 0
            ]

        return items

    @classmethod
    def _unit_prices(cls, items) -> list[Decimal]:
        """Expand cart lines to one entry per unit (final unit price).

        Reads the CartItem property, not ``product.final_price``, so a
        wholesale-bound cart credits BxGY discounts against the price
        the buyer actually pays — retail carts resolve identically.
        """
        units: list[Decimal] = []
        for item in items:
            unit = Decimal(str(item.final_price.amount))
            units.extend([unit] * item.quantity)
        return units

    @classmethod
    def _bxgy_amount(
        cls, promotion: Promotion, cart_items, currency: str
    ) -> Money:
        """Buy-X-get-Y-discounted, on units already in the cart.

        Two deterministic modes (documented on the admin form):

        - ``get_products`` EMPTY — same-pool: every group of
          ``buy_quantity + get_quantity`` eligible units earns one
          application; the CHEAPEST ``get_quantity`` units per
          application are discounted by ``get_discount_percent``.
          ("Buy 2 get 1 free" needs 3 units in the cart.)
        - ``get_products`` SET — reward-pool: applications =
          ``floor(eligible buy units / buy_quantity)``; the cheapest
          reward units already in the cart (up to ``applications ×
          get_quantity``) are discounted. Reward units that also match
          the buy scope still count on the buy side — the rule stays
          monotonic as the shopper adds items.
        """
        buy_qty = promotion.buy_quantity or 0
        get_qty = promotion.get_quantity or 0
        if buy_qty < 1 or get_qty < 1:
            return Money(Decimal("0"), currency)

        buy_units = cls._unit_prices(cls._matching_items(promotion, cart_items))
        reward_ids = set(promotion.get_products.values_list("id", flat=True))

        if not reward_ids:
            group_size = buy_qty + get_qty
            applications = len(buy_units) // group_size
            if applications < 1:
                return Money(Decimal("0"), currency)
            discounted_units = sorted(buy_units)[: applications * get_qty]
        else:
            applications = len(buy_units) // buy_qty
            if applications < 1:
                return Money(Decimal("0"), currency)
            reward_units = cls._unit_prices(
                [item for item in cart_items if item.product_id in reward_ids]
            )
            if not reward_units:
                return Money(Decimal("0"), currency)
            discounted_units = sorted(reward_units)[: applications * get_qty]

        pct = promotion.get_discount_percent / Decimal("100")
        amount = sum(discounted_units, Decimal("0")) * pct
        if promotion.max_discount_amount:
            amount = min(
                amount, Decimal(str(promotion.max_discount_amount.amount))
            )
        return Money(_quantize(amount), currency)

    @classmethod
    def _gift_entitlement(cls, promotion: Promotion) -> GiftEntitlement | None:
        """FREE_GIFT reward: the (single) configured gift product."""
        gift_product = (
            promotion.get_products.filter(active=True).order_by("pk").first()
        )
        if gift_product is None:
            logger.warning(
                "FREE_GIFT promotion %s has no active gift product "
                "configured — skipping",
                promotion.pk,
            )
            return None
        quantity = promotion.get_quantity or 1
        return GiftEntitlement(promotion, gift_product, quantity)

    @classmethod
    def _resolve_stacking(
        cls,
        monetary: list[AppliedPromotion],
        result: CartDiscountResult,
    ) -> list[AppliedPromotion]:
        """Stackables combine; the best non-stackable applies alone —
        whichever set discounts more wins."""
        stackables = sorted(
            (e for e in monetary if e.promotion.stackable),
            key=lambda e: (e.promotion.priority, e.promotion.pk),
        )
        non_stackables = [e for e in monetary if not e.promotion.stackable]

        stack_total = sum((e.amount.amount for e in stackables), Decimal("0"))
        best_single = max(
            non_stackables, key=lambda e: e.amount.amount, default=None
        )

        if best_single is None:
            return stackables
        if not stackables or best_single.amount.amount >= stack_total:
            chosen = [best_single]
            losers = stackables + [
                e for e in non_stackables if e is not best_single
            ]
        else:
            chosen = stackables
            losers = non_stackables

        for loser in losers:
            if loser.code is not None:
                result.rejected.append(
                    (
                        loser.code.code,
                        str(CouponRejectionReason.COMBINATION_DISALLOWED),
                    )
                )
        return chosen


class CouponService:
    """Attach/detach coupon codes on a cart."""

    @classmethod
    def apply(cls, cart, raw_code: str, *, user=None) -> CartDiscountResult:
        if not PromotionEngine.is_enabled():
            raise CouponError(CouponRejectionReason.INVALID)

        normalized = (raw_code or "").strip().upper()
        try:
            code = PromotionCode.objects.select_related("promotion").get(
                code=normalized,
                is_active=True,
                promotion__trigger=PromotionTrigger.CODE,
            )
        except PromotionCode.DoesNotExist as exc:
            raise CouponError(CouponRejectionReason.INVALID) from exc

        if not code.promotion.is_live:
            raise CouponError(
                PromotionEngine._dead_window_reason(code.promotion)
            )

        with transaction.atomic():
            # v1 policy: one coupon per cart — applying a new code
            # replaces the previous one (schema supports several).
            CartPromotionCode.objects.filter(cart=cart).delete()
            CartPromotionCode.objects.create(cart=cart, code=code)

        result = PromotionEngine.evaluate(cart, user=user)
        rejected = {c: r for c, r in result.rejected}
        if normalized in rejected:
            # The code exists but this cart/customer cannot use it —
            # detach it again and surface the precise reason.
            CartPromotionCode.objects.filter(cart=cart).delete()
            raise CouponError(rejected[normalized])
        return result

    @classmethod
    def remove(cls, cart) -> None:
        CartPromotionCode.objects.filter(cart=cart).delete()

    @classmethod
    def clear_after_order(cls, cart) -> None:
        """Detach codes once the order is placed (the redemption rows
        are the durable record)."""
        CartPromotionCode.objects.filter(cart=cart).delete()
