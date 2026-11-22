"""The promotions a cart is evaluated against, and whether each one may
apply to it."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from promotion.engine.scope import attach_category_scopes, matching_items
from promotion.enum import CouponRejectionReason
from promotion.models import (
    CartPromotionCode,
    Promotion,
    PromotionCode,
    PromotionRedemption,
)


def collect_candidates(
    cart, *, lock: bool
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
        Promotion.objects.filter(pk__in={*automatic_ids, *code_promotion_ids})
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
            rejected.append((code.code, str(dead_window_reason(promotion))))
            continue
        candidates.append((promotion, code))

    attach_category_scopes([promotion for promotion, _code in candidates])

    return candidates, rejected


def automatic_candidates() -> list[tuple[Promotion, PromotionCode | None]]:
    """The live automatic promotions, as candidates for a cart no code
    is applied to: what ``collect_candidates`` gives such a cart,
    without a lock."""
    promotions = list(
        Promotion.objects.live()
        .automatic()
        .with_scope_relations()
        .order_by("pk")
    )
    attach_category_scopes(promotions)
    return [(promotion, None) for promotion in promotions]


def dead_window_reason(promotion: Promotion) -> CouponRejectionReason:
    """Why a non-live promotion is refused: scheduled vs over."""
    if (
        promotion.is_active
        and promotion.starts_at
        and promotion.starts_at > timezone.now()
    ):
        return CouponRejectionReason.NOT_STARTED
    return CouponRejectionReason.EXPIRED


def check_eligibility(
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
        and not _is_code_owner(code, user, email)
    ):
        return str(CouponRejectionReason.USER_INELIGIBLE)

    # Scope before minimums: a code for chargers on a cart of cables
    # is "not applicable", not "minimum not met", and it must not be
    # accepted just because it would discount nothing.
    matching = matching_items(promotion, cart_items)
    if cart_items and not matching:
        return str(CouponRejectionReason.NOT_APPLICABLE)

    if promotion.min_subtotal and items_total < Decimal(
        str(promotion.min_subtotal.amount)
    ):
        return str(CouponRejectionReason.MINIMUM_NOT_MET)

    if promotion.min_quantity:
        eligible_units = sum(item.quantity for item in matching)
        if eligible_units < promotion.min_quantity:
            return str(CouponRejectionReason.MINIMUM_NOT_MET)

    if promotion.first_order_only:
        from order.models.order import Order

        identity = _identity_q(user, email)
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
        identity = _identity_q(user, email)
        used = (
            redemptions.filter(identity).count() if identity is not None else 0
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
        Q(**{f"{email_field}__iexact": address}) for address in sorted(emails)
    )
    if not clauses:
        return None

    predicate = clauses[0]
    for extra in clauses[1:]:
        predicate |= extra
    return predicate


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
