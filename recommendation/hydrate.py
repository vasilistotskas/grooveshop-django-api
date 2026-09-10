"""Turn engine output into serialized products — per request, never
cached.

The engine returns product IDs plus a reason. Serializing here, with
the request in ``context``, is what applies the viewer's pricing
context and locale; caching the serialized form would hand a B2B
price to a retail shopper. ``Product.objects.for_list()`` carries the
prefetches ``ProductSerializer`` needs, so the cost is constant in the
number of items rather than a query per product (the same lesson the
cart recommenders learned in ``cart/serializers/item.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from recommendation.engine import Suggestion


def hydrate_pairs(
    suggestions: list[Suggestion], *, context: Mapping[str, Any]
) -> list[tuple[Suggestion, dict[str, Any]]]:
    """``(suggestion, serialized product)`` pairs in engine order.

    A product that vanished between ranking and hydration (deleted,
    deactivated) is dropped rather than raising; the engine's guard
    ran moments ago, so this is a race, not a bug.
    """
    if not suggestions:
        return []

    from product.models import Product
    from product.serializers.product import ProductSerializer

    ids = [s.product_id for s in suggestions]
    by_id = {p.id: p for p in Product.objects.for_list().filter(id__in=ids)}
    ordered = [
        (s, by_id[s.product_id]) for s in suggestions if s.product_id in by_id
    ]
    serialized = ProductSerializer(
        [product for _, product in ordered], many=True, context=context
    ).data
    return [(s, data) for (s, _), data in zip(ordered, serialized, strict=True)]


def hydrate_products(
    suggestions: list[Suggestion], *, context: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Plain serialized products, engine order — the shape the cart's
    existing ``recommendations`` field already promises."""
    return [data for _, data in hydrate_pairs(suggestions, context=context)]
