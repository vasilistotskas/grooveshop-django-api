"""Bought together: products that shared an order with the seed.

Scored by confidence, ``P(candidate | seed)`` — of the orders in the
window that held the seed, the share that also held the candidate. A
pair seen in fewer than ``RECOMMENDATION_CO_PURCHASE_MIN_SUPPORT``
orders is dropped: two coincidences are not a pattern.

Declines until the store has
``RECOMMENDATION_CO_PURCHASE_MIN_ORDERS`` orders with two or more lines.
Below that every pair is a handful of orders and the strip would show
noise, which is exactly what a store with one-item baskets (most of
them, early on) would get from an aggregation that never says no.

Precomputed: an aggregation over every order is not a request-path
query. Canceled orders are not purchases and are left out; returned
and refunded ones still are, since the shopper chose those products
together.

Two queries per batch of seeds, whatever the batch size.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from recommendation.enum import StrategyCode
from recommendation.strategies.base import (
    Candidate,
    RecommendationStrategy,
    TenantContext,
    register_strategy,
)
from tenant.models import TenantPlan


@register_strategy
class CoPurchaseStrategy(RecommendationStrategy):
    code = StrategyCode.CO_PURCHASE
    label = StrategyCode.CO_PURCHASE.label
    min_plan = TenantPlan.PRO
    precompute = True

    def is_available(self, ctx: TenantContext) -> bool:
        if not super().is_available(ctx):
            return False
        return (
            ctx.multi_item_order_count
            >= settings.RECOMMENDATION_CO_PURCHASE_MIN_ORDERS
        )

    def candidates(self, product_id: int, limit: int) -> list[Candidate]:
        return self.candidates_for([product_id], limit).get(product_id, [])

    def candidates_for(
        self, seed_ids: Sequence[int], limit: int
    ) -> dict[int, list[Candidate]]:
        from order.enum.status import OrderStatus
        from order.models.item import OrderItem
        from product.models import Product

        since = timezone.now() - timedelta(
            days=settings.RECOMMENDATION_CO_PURCHASE_WINDOW_DAYS
        )
        purchases = OrderItem.objects.filter(
            order__created_at__gte=since
        ).exclude(order__status=OrderStatus.CANCELED)

        # Query 1: the orders each seed was bought in.
        orders_of: dict[int, set[int]] = defaultdict(set)
        for product_id, order_id in purchases.filter(
            product_id__in=list(seed_ids)
        ).values_list("product_id", "order_id"):
            orders_of[product_id].add(order_id)
        if not orders_of:
            return {}

        # Query 2: every sellable product in those orders.
        order_ids = set().union(*orders_of.values())
        lines_of: dict[int, set[int]] = defaultdict(set)
        for order_id, product_id in purchases.filter(
            order_id__in=order_ids,
            product__in=Product.objects.active(),
        ).values_list("order_id", "product_id"):
            lines_of[order_id].add(product_id)

        min_support = settings.RECOMMENDATION_CO_PURCHASE_MIN_SUPPORT
        out: dict[int, list[Candidate]] = {}
        for seed_id, seed_orders in orders_of.items():
            together: dict[int, int] = defaultdict(int)
            for order_id in seed_orders:
                for product_id in lines_of.get(order_id, ()):
                    if product_id != seed_id:
                        together[product_id] += 1
            scored = sorted(
                (
                    (count / len(seed_orders), product_id)
                    for product_id, count in together.items()
                    if count >= min_support
                ),
                key=lambda pair: (-pair[0], pair[1]),
            )[:limit]
            if scored:
                out[seed_id] = [
                    Candidate(product_id=product_id, score=round(score, 4))
                    for score, product_id in scored
                ]
        return out
