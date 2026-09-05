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
