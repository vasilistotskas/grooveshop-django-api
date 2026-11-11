"""Which products are "on offer" — the one definition every surface uses.

A product is on offer when it carries a product-level markdown
(``discount_percent > 0``) or when a live, automatic, scoped promotion
covers it. The search index (``on_offer``), the product list/detail API
(``offer_kind``) and the search-result cards all read the SQL expression
built here, so the filter a shopper clicks and the badge a card shows
cannot disagree.

Rules for the promotion branch (``covered_by_live_promotion``):

* AUTOMATIC only — a code promotion advertises nothing until typed.
* ``target_scope`` PRODUCTS or CATEGORIES (category scope includes the
  subtree, like ``PromotionEngine._matching_items``). ORDER-scope
  promotions are cart-level and never mark a product.
* Benefit PERCENTAGE, FIXED_AMOUNT, BXGY or FREE_GIFT; FREE_SHIPPING is
  a shipping perk, not a price offer.
* Live (active, inside the window) and, through ``publicly_listable``,
  not past ``usage_limit_total`` — the same filter the ``/offers`` page
  uses, so no surface advertises what the cart would refuse.
* ``excluded_products`` / ``excluded_categories`` (subtree) remove the
  product from the promotion.
* ``exclude_discounted_products`` needs no clause: it only removes
  products that carry a markdown, and a markdown product is already
  ``MARKDOWN``, which outranks ``PROMOTION``.

The reward side of a BXGY/FREE_GIFT promotion (the gift product) is not
marked: the offer is on the products the shopper buys.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import (
    Case,
    CharField,
    Exists,
    OuterRef,
    Q,
    Value,
    When,
)

from promotion.enum import (
    BenefitType,
    OfferKind,
    PromotionTrigger,
    TargetScope,
)
from promotion.models import Promotion


def live_offer_promotions():
    """Promotions that can make a product count as on offer right now."""
    return (
        Promotion.objects.live()
        .publicly_listable()
        .filter(
            trigger=PromotionTrigger.AUTOMATIC,
            target_scope__in=(TargetScope.PRODUCTS, TargetScope.CATEGORIES),
        )
        .exclude(benefit_type=BenefitType.FREE_SHIPPING)
    )


def price_reducing_promotions():
    """Promotions that lower a product's unit price whatever the cart holds.

    The subset of ``live_offer_promotions`` that is a price reduction in
    itself: a percentage or fixed amount off, with no minimum quantity,
    no minimum subtotal, no first-order restriction and no per-customer
    limit. A conditional promotion (and a buy-X-get-Y or gift) is a deal
    on a basket, not a price the product is offered at, so it neither
    moves the price a listing shows nor is a reduction to announce.
    """
    return live_offer_promotions().filter(
        benefit_type__in=(BenefitType.PERCENTAGE, BenefitType.FIXED_AMOUNT),
        min_quantity__isnull=True,
        min_subtotal__isnull=True,
        first_order_only=False,
        usage_limit_per_customer__isnull=True,
    )


def load_price_reducing_promotions() -> list[Promotion]:
    """``price_reducing_promotions`` ready for ``PromotionEngine``.

    Empty while promotions are off (plan or merchant toggle): the cart
    engine then grants nothing, so no price may reflect one. Scope
    relations and category subtrees are loaded once, so pricing many
    products against the result costs no further queries.
    """
    from promotion.services import PromotionEngine

    if not PromotionEngine.is_enabled():
        return []
    promotions = list(
        price_reducing_promotions().with_scope_relations().order_by("pk")
    )
    PromotionEngine.attach_category_scopes(promotions)
    return promotions


def _in_category_subtree(categories, prefix: str, depth: int):
    """Narrow ``categories`` to the ones that contain the product's category.

    MPTT: a category contains another when it shares its tree and its
    ``lft``/``rght`` range spans the other's. ``depth`` is how many
    subquery levels sit between ``categories`` and the product row.
    """

    def ref(field: str):
        outer = OuterRef(f"{prefix}category__{field}")
        for _level in range(depth - 1):
            outer = OuterRef(outer)
        return outer

    return categories.filter(
        tree_id=ref("tree_id"), lft__lte=ref("lft"), rght__gte=ref("rght")
    )


def covered_by_live_promotion(prefix: str = ""):
    """``Exists`` expression: a live offer promotion covers the product.

    ``prefix`` locates the product from the queryset's own model:
    ``""`` on ``Product``, ``"master__"`` on ``ProductTranslation``.
    """
    from product.models.category import ProductCategory

    nested_product_pk = OuterRef(OuterRef(f"{prefix}pk"))

    includes_product = Exists(
        Promotion.products.through.objects.filter(
            promotion_id=OuterRef("pk"), product_id=nested_product_pk
        )
    )
    includes_category = Exists(
        _in_category_subtree(
            ProductCategory.objects.filter(promotions=OuterRef("pk")),
            prefix,
            depth=2,
        )
    )
    excludes_product = Exists(
        Promotion.excluded_products.through.objects.filter(
            promotion_id=OuterRef("pk"), product_id=nested_product_pk
        )
    )
    excludes_category = Exists(
        _in_category_subtree(
            ProductCategory.objects.filter(
                excluded_from_promotions=OuterRef("pk")
            ),
            prefix,
            depth=2,
        )
    )

    promotions = (
        live_offer_promotions()
        .filter(
            Q(target_scope=TargetScope.PRODUCTS) & includes_product
            | Q(target_scope=TargetScope.CATEGORIES) & includes_category
        )
        .filter(~excludes_product & ~excludes_category)
    )
    return Exists(promotions)


def offer_kind_expression(prefix: str = "") -> Case:
    """SQL ``MARKDOWN`` / ``PROMOTION`` / ``NULL`` for a product row."""
    from promotion.services import PromotionEngine

    whens: list[When] = [
        When(
            Q(**{f"{prefix}discount_percent__gt": 0}),
            then=Value(OfferKind.MARKDOWN),
        )
    ]
    # Plan AND merchant toggle: with promotions off the cart engine
    # grants nothing, so advertising an offer would be a lie.
    if PromotionEngine.is_enabled():
        whens.append(
            When(
                covered_by_live_promotion(prefix),
                then=Value(OfferKind.PROMOTION),
            )
        )
    return Case(
        *whens,
        default=Value(None),
        output_field=CharField(null=True),
    )


def scope_product_ids(promotion_ids: Iterable[int]) -> set[int]:
    """Products the given promotions name, directly or by category.

    The superset whose on-offer status a change to those promotions can
    move: it deliberately ignores ``target_scope``, the exclusions and
    liveness, because the reindex it feeds is idempotent and the
    promotion may be mid-edit (scope just switched, exclusion just
    removed). Soft-deleted products are included; the indexer drops
    their documents.
    """
    from product.models.category import ProductCategory
    from product.models.product import Product

    ids = list(promotion_ids)
    if not ids:
        return set()
    in_scope_category = Exists(
        _in_category_subtree(
            ProductCategory.objects.filter(promotions__in=ids),
            "",
            depth=1,
        )
    )
    return set(
        Product._base_manager.filter(Q(promotions__in=ids) | in_scope_category)
        .order_by()
        .values_list("pk", flat=True)
        .distinct()
    )
