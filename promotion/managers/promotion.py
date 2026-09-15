from __future__ import annotations

from typing import Self

from django.db.models import Count, F, Q
from django.utils import timezone

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from promotion.enum import PromotionTrigger


def publishable_code_q(prefix: str = "") -> Q:
    """Codes a shopper may be shown.

    Excludes personal coupons (assigned to a user or an email) and
    single-use codes. The ``usage_limit=1`` exclusion is about the
    MECHANIC, not the remaining count: the field's own help text says
    "1 for single-use bulk codes", i.e. codes minted in bulk and handed
    out individually by email or print. Publishing any of those on a
    crawlable page is wrong whether or not it has been redeemed yet.
    A promotion-level ``usage_limit_total=1`` is the opposite case — a
    genuine first-come-first-served offer — and stays listed until it is
    actually taken, which ``publicly_listable`` below handles.

    ``prefix`` exists because the same condition is needed in two
    places with different anchors: a ``Count(filter=...)`` on
    ``Promotion`` must name the relation (``codes__is_active``), while a
    queryset on ``PromotionCode`` must not. Deriving both from one
    function keeps them from drifting apart.

    ``usage_limit`` is compared as ``IS NULL OR > 1`` rather than
    ``~Q(usage_limit=1)`` on purpose: a negated equality is not
    NULL-safe in SQL, and unlimited codes (the common case) carry NULL,
    so the tidier-looking form would exclude exactly the codes most
    worth advertising.
    """
    field = f"{prefix}__" if prefix else ""
    return Q(
        **{
            f"{field}is_active": True,
            f"{field}assigned_to__isnull": True,
            f"{field}assigned_to_email": "",
        }
    ) & (
        Q(**{f"{field}usage_limit__isnull": True})
        | Q(**{f"{field}usage_limit__gt": 1})
    )


class PromotionQuerySet(TranslatableOptimizedQuerySet):
    def live(self) -> Self:
        """Active promotions inside their schedule window."""
        now = timezone.now()
        return self.filter(is_active=True).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now),
            Q(ends_at__isnull=True) | Q(ends_at__gt=now),
        )

    def automatic(self) -> Self:
        return self.filter(trigger=PromotionTrigger.AUTOMATIC)

    def publicly_listable(self) -> Self:
        """Promotions a shopper may be told about, unprompted.

        Shared by all three discovery surfaces — the ``/offers`` page,
        the product page's offer panel, and the checkout coupon picker —
        because "may we advertise this?" must have exactly one answer.
        Combine with ``live()``; this method only adds the code and
        usage-limit conditions.
        """
        return (
            self.annotate(
                # distinct=True on both: two Counts over different
                # multi-valued relations fan the join out, and without
                # it each count would be multiplied by the other's row
                # count.
                publishable_code_count=Count(
                    "codes",
                    filter=publishable_code_q("codes"),
                    distinct=True,
                ),
                # ``usage_limit_total`` caps the ORDERS that used the
                # promotion, which is what PromotionRedemption counts —
                # matching PromotionEngine's own check so no surface
                # ever advertises an offer the cart would refuse with
                # USAGE_LIMIT_REACHED.
                redemption_count=Count("redemptions", distinct=True),
            )
            .filter(
                # AUTOMATIC needs no code; a CODE promotion is useless
                # to a shopper without one they are allowed to see.
                Q(trigger=PromotionTrigger.AUTOMATIC)
                | Q(publishable_code_count__gt=0)
            )
            .filter(
                # Written as an inclusive filter rather than exclude():
                # exclude() on a nullable annotation comparison drops
                # the unlimited rows too.
                Q(usage_limit_total__isnull=True)
                | Q(redemption_count__lt=F("usage_limit_total"))
            )
        )

    def for_list(self) -> Self:
        return self.with_translations()

    def for_detail(self) -> Self:
        return self.with_translations().prefetch_related(
            "codes", "products", "categories"
        )

    def with_scope_relations(self) -> Self:
        """Prefetch every relation the scope/exclusion walk reads.

        ``PromotionEngine._matching_items`` and the product-page
        relation walk both touch all five per candidate, so without
        this the cost grows with the number of live promotions.
        """
        return self.prefetch_related(
            "products",
            "categories",
            "excluded_products",
            "excluded_categories",
            "get_products",
        )


class PromotionManager(TranslatableOptimizedManager):
    queryset_class = PromotionQuerySet

    def live(self) -> PromotionQuerySet:
        return self.get_queryset().live()

    def automatic(self) -> PromotionQuerySet:
        return self.get_queryset().automatic()
