"""The engine runs on every cart read — it must not scale with promotions.

`_collect_candidates` selected the candidate promotions with no
`prefetch_related`, and `_matching_items` then asked each one for its
`products`, `categories`, `excluded_products` and `excluded_categories`
through `.values_list()`. So the cost grew with the number of ACTIVE
PROMOTIONS, not with cart size — and `evaluate()` runs on every cart
read, on the payment-intent path, and twice more during order creation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from promotion.enum import PromotionTrigger
from promotion.factories.promotion import PromotionFactory
from promotion.services import PromotionEngine
from tests.utils import count_queries

pytestmark = pytest.mark.django_db


def _live_promotions(count):
    """Eligible promotions, so each one actually reaches
    `_matching_items` — a promotion rejected on `min_subtotal`
    short-circuits before touching any of the M2Ms and would hide the
    cost entirely. `stackable` so they all evaluate rather than one
    winning and the rest being skipped."""
    for _ in range(count):
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal("1.0"),
            stackable=True,
        )


def test_cost_does_not_grow_with_the_number_of_live_promotions(
    enable_promotions, make_cart
):
    cart, _items = make_cart([(50, 1)])

    _live_promotions(2)
    with count_queries() as few:
        PromotionEngine.evaluate(cart)

    _live_promotions(6)
    with count_queries() as many:
        PromotionEngine.evaluate(cart)

    assert many.count == few.count, (
        f"evaluate() grew from {few.count} to {many.count} queries with "
        f"six more live promotions — the candidate queryset is missing "
        f"its prefetches, or _matching_items is bypassing them"
    )


def _live_gift_promotions(count):
    """Eligible FREE_GIFT promotions, each with its own gift product."""
    from djmoney.money import Money

    from product.factories.product import ProductFactory
    from promotion.enum import BenefitType

    for _ in range(count):
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_GIFT,
            get_quantity=1,
            min_subtotal=Money(Decimal(1), "EUR"),
            stackable=True,
        )
        promotion.get_products.add(ProductFactory(active=True))


def test_free_gift_promotions_do_not_each_cost_a_query(
    enable_promotions, make_cart
):
    """`_gift_entitlement` must read the prefetched rows, not re-query.

    `_collect_candidates` prefetches `get_products`, but a FILTERED
    related-manager call — `promotion.get_products.filter(active=True)` —
    does not use that cache. Django's own docs are explicit that a
    filtered prefetch is not served by the standard manager interface,
    so each eligible FREE_GIFT promotion added one query.
    """
    cart, _items = make_cart([(50, 1)])

    _live_gift_promotions(2)
    with count_queries() as few:
        PromotionEngine.evaluate(cart)

    _live_gift_promotions(6)
    with count_queries() as many:
        PromotionEngine.evaluate(cart)

    assert many.count == few.count, (
        f"evaluate() grew from {few.count} to {many.count} queries with "
        f"six more FREE_GIFT promotions — _gift_entitlement is "
        f"re-querying instead of reading the prefetched rows"
    )


def _category_promotions(count):
    """Category-scoped promotions, each with its OWN category.

    Distinct categories on purpose: a memo keyed on the id set would
    hide the cost, and the real catalogue has one promotion per
    department rather than many sharing a set.
    """
    from product.factories.category import ProductCategoryFactory
    from promotion.enum import TargetScope

    for _ in range(count):
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal("1.0"),
            stackable=True,
            target_scope=TargetScope.CATEGORIES,
        )
        promotion.categories.add(ProductCategoryFactory())


def test_category_scoped_promotions_do_not_each_cost_a_descendant_query(
    enable_promotions, make_cart
):
    """MPTT descendant expansion must be batched across candidates.

    `_matching_items` expands a category-scoped promotion's INCLUDED
    categories and every promotion's EXCLUDED ones, and each expansion
    was its own `get_descendants` query.
    """
    cart, _items = make_cart([(50, 1)])

    _category_promotions(2)
    with count_queries() as few:
        PromotionEngine.evaluate(cart)

    _category_promotions(6)
    with count_queries() as many:
        PromotionEngine.evaluate(cart)

    assert many.count == few.count, (
        f"evaluate() grew from {few.count} to {many.count} queries with "
        f"six more category-scoped promotions — descendant expansion is "
        f"still running once per promotion"
    )
