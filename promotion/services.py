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
from django.db.models import Prefetch, Q
from django.utils import timezone
from djmoney.money import Money
from extra_settings.models import Setting

from promotion.enum import (
    BenefitType,
    CouponRejectionReason,
    ProductPromotionRelation,
    PromotionTrigger,
    TargetScope,
)
from promotion.managers.promotion import publishable_code_q
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
        total = sum((entry.amount.amount for entry in self.applied), Decimal(0))
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
        from tenant.membership import tenant_plan_allows

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
        from b2b.services import (
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
            (item.total_price.amount for item in cart_items), Decimal(0)
        )

        candidates, rejected = cls._collect_candidates(cart, lock=lock)
        result.rejected.extend(rejected)

        monetary, applicable_free_shipping = cls._classify(
            candidates,
            cart_items=cart_items,
            items_total=items_total,
            currency=currency,
            user=user,
            email=email,
            result=result,
        )

        chosen = cls._resolve_stacking(monetary, result)
        chosen = cls._clamp(chosen, items_total, currency)

        result.applied = [e for e in chosen if e.amount.amount > 0]
        result.free_shipping = applicable_free_shipping
        return result

    @classmethod
    def _classify(
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
        picker (``CouponService.available``) has to run exactly this
        classification over a different candidate set — an unattached
        coupon each time — and a second copy of the rules would drift
        from the one the cart is actually charged with.
        """
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
            amount = cls._amount_for(
                promotion, cart_items, items_total, currency
            )
            if amount.amount <= 0:
                continue
            monetary.append(AppliedPromotion(promotion, code, amount))

        return monetary, applicable_free_shipping

    @classmethod
    def _amount_for(
        cls,
        promotion: Promotion,
        cart_items,
        items_total: Decimal,
        currency: str,
    ) -> Money:
        """The discount one monetary promotion is worth on this cart."""
        if promotion.benefit_type == BenefitType.BXGY:
            return cls._bxgy_amount(promotion, cart_items, currency)
        return cls._benefit_amount(promotion, cart_items, items_total, currency)

    @staticmethod
    def _clamp(
        chosen: list[AppliedPromotion],
        items_total: Decimal,
        currency: str,
    ) -> list[AppliedPromotion]:
        """Never discount below zero items value.

        Returns NEW entries rather than mutating the ones handed in:
        the coupon picker clamps the same baseline entries once per
        candidate coupon, and an in-place rewrite would carry each
        pass's clamping into the next one.
        """
        clamped: list[AppliedPromotion] = []
        running = Decimal(0)
        for entry in chosen:
            available = items_total - running
            amount = (
                Decimal(0)
                if available <= 0
                else _quantize(min(entry.amount.amount, available))
            )
            running += amount
            clamped.append(
                AppliedPromotion(
                    entry.promotion, entry.code, Money(amount, currency)
                )
            )
        return clamped

    @classmethod
    def gift_weight_grams(cls, result: CartDiscountResult) -> int:
        """Extra parcel weight the entitled gift items add.

        Shipping quotes are weight-banded (ACS) — every surface that
        prices shipping (payment-intent, verification, creation) must
        include the gifts or the courier voucher upcharges later.
        """
        if not result.gift_items:
            return 0
        from shipping.utils import (
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

        # `_matching_items` and `_gift_entitlement` walk five relations
        # for EVERY candidate, so without the prefetch the engine's
        # cost grows with the number of live promotions — and
        # `evaluate()` runs on every cart read, on the payment-intent
        # path, and twice more during order creation.
        promotions_qs = (
            Promotion.objects.filter(
                pk__in={*automatic_ids, *code_promotion_ids}
            )
            .with_scope_relations()
            .order_by("pk")
        )
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

        cls.attach_category_scopes(
            [promotion for promotion, _code in candidates]
        )

        return candidates, rejected

    @classmethod
    def attach_category_scopes(cls, promotions: list[Promotion]) -> None:
        """Expand every promotion's categories from ONE descendant query
        and hang the result on the instance.

        The Promotion objects are built fresh by the caller for a single
        evaluation, so the attributes cannot outlive it or cross a
        tenant. ``ProductPromotionService`` needs the same expansion to
        decide whether a product's category is in scope, which is why
        this is public rather than inlined in ``_collect_candidates``.
        """
        root_category_ids: set[int] = set()
        for promotion in promotions:
            root_category_ids.update(c.id for c in promotion.categories.all())
            root_category_ids.update(
                c.id for c in promotion.excluded_categories.all()
            )
        index = cls._descendant_index(root_category_ids)
        for promotion in promotions:
            promotion._included_category_ids = cls._expand(
                promotion.categories, index
            )
            promotion._excluded_category_ids = cls._expand(
                promotion.excluded_categories, index
            )

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
            from order.models.order import Order

            identity = cls._identity_q(user, email)
            if identity is not None:
                has_orders = Order.objects.filter(identity).exists()
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
            identity = cls._identity_q(user, email)
            used = (
                redemptions.filter(identity).count()
                if identity is not None
                else 0
            )
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
    def _identity_q(
        user,
        email: str | None,
        *,
        user_field: str = "user",
        email_field: str = "email",
    ):
        """Match rows belonging to this shopper, by account OR by email.

        Both signals have to be UNIONED, never chosen between. A guest
        checkout writes `user=None, email=...`, and nothing backfills the
        account onto that row when the shopper later registers — so a
        check that looks only at `user=` once someone is authenticated
        cannot see their own guest history, and `first_order_only` and
        `usage_limit_per_customer` each hand the discount out a second
        time. `_is_code_owner` below already unions the two; these were
        the sites that branched instead.

        Returns None when the shopper is anonymous and no email is known
        yet, so callers take their own "identity unknown" branch rather
        than silently matching everything.
        """
        authenticated = user is not None and getattr(
            user, "is_authenticated", False
        )
        emails = set()
        if authenticated and user.email:
            emails.add(user.email.lower())
        if email:
            emails.add(email.lower())

        clauses = []
        if authenticated:
            clauses.append(Q(**{user_field: user}))
        clauses.extend(
            Q(**{f"{email_field}__iexact": address})
            for address in sorted(emails)
        )
        if not clauses:
            return None

        predicate = clauses[0]
        for extra in clauses[1:]:
            predicate |= extra
        return predicate

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
            Decimal(0),
        )
        if base <= 0:
            return Money(Decimal(0), currency)

        if promotion.benefit_type == BenefitType.PERCENTAGE:
            amount = base * promotion.benefit_value / Decimal(100)
        else:  # FIXED_AMOUNT
            amount = min(promotion.benefit_value, base)

        if promotion.max_discount_amount:
            amount = min(
                amount, Decimal(str(promotion.max_discount_amount.amount))
            )
        return Money(_quantize(amount), currency)

    @classmethod
    def _descendant_index(cls, root_ids: set[int]) -> dict[int, set[int]]:
        """``category id -> its descendant ids``, for every root, in ONE query.

        This used to be a per-call ``get_descendants`` inside
        ``_expanded_category_ids``, and ``_matching_items`` calls that
        for a category-scoped promotion's INCLUDED categories and for
        every promotion's EXCLUDED ones — so the engine still grew with
        the number of promotions carrying categories, which is the cost
        this change set exists to remove. Measured before: 13 queries at
        two category-scoped promotions, 25 at eight.

        A memo keyed on the id set would not have helped, because
        distinct promotions usually carry distinct categories. One query
        over the union does: MPTT gives every row a ``tree_id`` and an
        ``lft``/``rght`` range, so each root's descendants are
        recoverable from the same result set in memory.
        """
        if not root_ids:
            return {}
        from product.models.category import ProductCategory

        rows = list(
            ProductCategory.objects.filter(pk__in=root_ids)
            .get_descendants(include_self=True)
            .values("id", "tree_id", "lft", "rght")
        )
        roots = {row["id"]: row for row in rows if row["id"] in root_ids}
        return {
            root_id: {
                row["id"]
                for row in rows
                if row["tree_id"] == root["tree_id"]
                and root["lft"] <= row["lft"] <= root["rght"]
            }
            for root_id, root in roots.items()
        }

    @classmethod
    def _category_ids(cls, promotion: Promotion, side: str) -> set[int]:
        """The expanded category ids ``_collect_candidates`` attached.

        Falls back to expanding on the spot for a Promotion that did not
        come through candidate collection — the helper is also reachable
        from tests and from any future caller, and a silently empty set
        would quietly widen a promotion's scope rather than fail.
        """
        attr = f"_{side}_category_ids"
        cached = getattr(promotion, attr, None)
        if cached is not None:
            return cached

        source = (
            promotion.categories
            if side == "included"
            else promotion.excluded_categories
        )
        ids = {category.id for category in source.all()}
        return cls._expand(source, cls._descendant_index(ids))

    @staticmethod
    def _expand(categories_qs, index: dict[int, set[int]]) -> set[int]:
        """Union the prefetched categories' descendants from the index."""
        ids = [category.id for category in categories_qs.all()]
        if not ids:
            return set()
        return set().union(*(index.get(cid, {cid}) for cid in ids))

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
            # `.all()`, not `.values_list()`: values_list builds a
            # new queryset and always queries, ignoring the prefetch.
            include_ids = {p.id for p in promotion.products.all()}
            items = [
                item for item in cart_items if item.product_id in include_ids
            ]
        elif promotion.target_scope == TargetScope.CATEGORIES:
            include_categories = cls._category_ids(promotion, "included")
            items = [
                item
                for item in cart_items
                if item.product.category_id in include_categories
            ]
        else:
            items = list(cart_items)

        excluded_ids = {p.id for p in promotion.excluded_products.all()}
        if excluded_ids:
            items = [
                item for item in items if item.product_id not in excluded_ids
            ]

        excluded_categories = cls._category_ids(promotion, "excluded")
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
            return Money(Decimal(0), currency)

        buy_units = cls._unit_prices(cls._matching_items(promotion, cart_items))
        reward_ids = {p.id for p in promotion.get_products.all()}

        if not reward_ids:
            group_size = buy_qty + get_qty
            applications = len(buy_units) // group_size
            if applications < 1:
                return Money(Decimal(0), currency)
            discounted_units = sorted(buy_units)[: applications * get_qty]
        else:
            applications = len(buy_units) // buy_qty
            if applications < 1:
                return Money(Decimal(0), currency)
            reward_units = cls._unit_prices(
                [item for item in cart_items if item.product_id in reward_ids]
            )
            if not reward_units:
                return Money(Decimal(0), currency)
            discounted_units = sorted(reward_units)[: applications * get_qty]

        pct = promotion.get_discount_percent / Decimal(100)
        amount = sum(discounted_units, Decimal(0)) * pct
        if promotion.max_discount_amount:
            amount = min(
                amount, Decimal(str(promotion.max_discount_amount.amount))
            )
        return Money(_quantize(amount), currency)

    @classmethod
    def _gift_entitlement(cls, promotion: Promotion) -> GiftEntitlement | None:
        """FREE_GIFT reward: the (single) configured gift product.

        Chosen in memory from the prefetched rows. ``_collect_candidates``
        prefetches ``get_products``, and a FILTERED related-manager query
        does not use that cache — ``.filter(active=True)`` builds a new
        queryset and always hits the database, so every eligible
        FREE_GIFT promotion cost one more query, which is the exact
        defect this change set removes elsewhere in the same file.
        ``min`` over the cached rows reproduces
        ``.filter(active=True).order_by("pk").first()`` exactly: lowest
        pk among the active ones, or None.
        """
        gift_product = min(
            (p for p in promotion.get_products.all() if p.active),
            key=lambda p: p.pk,
            default=None,
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

        stack_total = sum((e.amount.amount for e in stackables), Decimal(0))
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


@dataclass
class ProductOffer:
    """One live offer a product's page should advertise, with the
    reason it is relevant to THAT product."""

    promotion: Promotion
    relation: ProductPromotionRelation


class ProductPromotionService:
    """Which live offers apply to a single product.

    The ``/offers`` page answers "what is running"; this answers "what
    is running FOR THIS ITEM", which is the question a shopper actually
    has while looking at a product. Scope and exclusions are resolved
    with the same rules ``PromotionEngine._matching_items`` uses at
    cart time, so the page never advertises an offer the cart would
    silently skip.

    Read-only and cart-free: it cannot know whether the eventual cart
    will clear a minimum subtotal, so the conditions travel with the
    row and the storefront renders them as fine print.
    """

    # Specific beats general, for both the relation chosen per
    # promotion and the order the page lists them in. REWARD outranks
    # CATEGORY because "you can get this one free" is a stronger claim
    # about the item in front of the shopper than "its category is on
    # offer"; it loses to PRODUCT, which is the promotion naming the
    # item outright.
    _RELATION_RANK = {
        ProductPromotionRelation.PRODUCT: 0,
        ProductPromotionRelation.REWARD: 1,
        ProductPromotionRelation.CATEGORY: 2,
        ProductPromotionRelation.ORDER: 3,
    }

    @classmethod
    def for_product(cls, product) -> list[ProductOffer]:
        """Publicly advertisable live offers touching ``product``."""
        if not PromotionEngine.is_enabled():
            return []

        promotions = list(
            Promotion.objects.live()
            .publicly_listable()
            .for_list()
            .with_scope_relations()
            .prefetch_related(
                Prefetch(
                    "codes",
                    queryset=PromotionCode.objects.filter(
                        publishable_code_q()
                    ).order_by("created_at"),
                    to_attr="publishable_codes",
                ),
                "products__translations",
                "get_products__translations",
                "categories__translations",
            )
            .order_by("priority", "id")
        )
        PromotionEngine.attach_category_scopes(promotions)

        offers = [
            ProductOffer(promotion, relation)
            for promotion in promotions
            if (relation := cls._relation(promotion, product)) is not None
        ]
        offers.sort(
            key=lambda offer: (
                cls._RELATION_RANK[offer.relation],
                offer.promotion.priority,
                offer.promotion.id,
            )
        )
        return offers

    @classmethod
    def _relation(
        cls, promotion: Promotion, product
    ) -> ProductPromotionRelation | None:
        """Why this promotion matters on this product's page, or None.

        The buy side is checked first and only counts when the product
        survives the promotion's exclusions — an excluded product is
        exactly the case where showing the offer would be a lie. The
        reward side is a separate claim ("this item is the gift") and
        carries no such exclusions, because the exclusion lists
        constrain what the shopper must BUY, not what they receive.
        """
        buy = cls._buy_relation(promotion, product)
        reward = cls._reward_relation(promotion, product)
        if buy is None:
            return reward
        if reward is None:
            return buy
        return min(buy, reward, key=lambda rel: cls._RELATION_RANK[rel])

    @staticmethod
    def _buy_relation(
        promotion: Promotion, product
    ) -> ProductPromotionRelation | None:
        excluded_product_ids = {p.id for p in promotion.excluded_products.all()}
        if product.id in excluded_product_ids:
            return None
        if product.category_id in promotion._excluded_category_ids:
            return None
        if (
            promotion.exclude_discounted_products
            and (product.discount_percent or 0) > 0
        ):
            return None

        if promotion.target_scope == TargetScope.PRODUCTS:
            included = {p.id for p in promotion.products.all()}
            return (
                ProductPromotionRelation.PRODUCT
                if product.id in included
                else None
            )
        if promotion.target_scope == TargetScope.CATEGORIES:
            return (
                ProductPromotionRelation.CATEGORY
                if product.category_id in promotion._included_category_ids
                else None
            )
        return ProductPromotionRelation.ORDER

    @staticmethod
    def _reward_relation(
        promotion: Promotion, product
    ) -> ProductPromotionRelation | None:
        """Whether the shopper RECEIVES this product from the offer.

        FREE_GIFT mirrors ``PromotionEngine._gift_entitlement`` exactly
        — lowest-pk ACTIVE gift product — rather than "is in the
        get_products list", so a mis-configured promotion with two
        gifts never promises the one the engine would never hand out.
        """
        if promotion.benefit_type == BenefitType.FREE_GIFT:
            entitlement = PromotionEngine._gift_entitlement(promotion)
            return (
                ProductPromotionRelation.REWARD
                if entitlement is not None
                and entitlement.product.id == product.id
                else None
            )
        if promotion.benefit_type != BenefitType.BXGY:
            return None
        reward_ids = {p.id for p in promotion.get_products.all()}
        return (
            ProductPromotionRelation.REWARD
            if product.id in reward_ids
            else None
        )


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
    # Zero is a legitimate answer for an eligible coupon whose products
    # are not in the cart, or one a better automatic offer outranks.
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
    ``_collect_candidates`` pass for the automatic baseline, then the
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
            baseline_monetary, items_total, currency
        )

        PromotionEngine.attach_category_scopes(
            [code.promotion for code in codes]
        )
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
        ``_matching_items`` then walks, and the version that looked
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
        candidates, _rejected = PromotionEngine._collect_candidates(
            cart, lock=False
        )
        automatic = [
            (promotion, code) for promotion, code in candidates if code is None
        ]
        return PromotionEngine._classify(
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
        items_total: Decimal,
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
        chosen = PromotionEngine._resolve_stacking(monetary, scratch)
        clamped = PromotionEngine._clamp(chosen, items_total, currency)
        total = sum((entry.amount.amount for entry in clamped), Decimal(0))
        losers = {
            rejected_code
            for rejected_code, reason in scratch.rejected
            if reason == str(CouponRejectionReason.COMBINATION_DISALLOWED)
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
                reason=str(PromotionEngine._dead_window_reason(promotion)),
                amount=zero,
                free_shipping=False,
                applied=applied,
            )

        reason = PromotionEngine._check_eligibility(
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
            return CouponOption(
                promotion=promotion,
                code=code,
                reason=None,
                amount=zero,
                # Only claim shipping as this coupon's doing when an
                # automatic offer is not already waiving it.
                free_shipping=not baseline_shipping,
                applied=applied,
            )
        if promotion.benefit_type == BenefitType.FREE_GIFT:
            entitlement = PromotionEngine._gift_entitlement(promotion)
            return CouponOption(
                promotion=promotion,
                code=code,
                # A gift promotion with no usable gift product is a
                # misconfiguration, not an offer — refuse it the same
                # way the engine silently drops it.
                reason=None
                if entitlement is not None
                else str(CouponRejectionReason.INVALID),
                amount=zero,
                free_shipping=False,
                applied=applied,
            )

        amount = PromotionEngine._amount_for(
            promotion, cart_items, items_total, currency
        )
        if amount.amount <= 0:
            # Worth nothing on this cart — its products are not in it,
            # or the benefit computes to zero. ``_classify`` drops such
            # a promotion before stacking, so no rejection is raised and
            # ``apply`` accepts the code; it simply moves no money. The
            # shopper is told that rather than left watching an
            # unchanged total.
            return CouponOption(
                promotion=promotion,
                code=code,
                reason=None,
                amount=zero,
                free_shipping=False,
                applied=applied,
            )

        entry = AppliedPromotion(promotion, code, amount)
        total_with, losers = cls._stacked_total(
            [*baseline_monetary, entry], items_total, currency
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
