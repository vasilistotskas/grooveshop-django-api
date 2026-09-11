"""Vertical presets: the slot configuration a store starts from.

A preset is a per-surface chain + weights + limits, keyed by
``tenant.models.StoreVertical`` — the platform-level fact about what a
store sells. Provisioning seeds the tenant's vertical; the PreSync job's
``backfill_recommendation_slots`` seeds any tenant that has no slots
yet; the slot admin's "Reset to preset" re-applies it on demand. Seeding
never overwrites a row a merchant has edited — ``get_or_create`` only;
only the explicit reset does.

Every preset lists the Free-tier strategies first and the plan-gated
ones after: the engine skips a strategy the plan does not allow, so a
preset can name ``semantic`` for every vertical and it simply starts
contributing the day the store upgrades. Order is priority, not tier.

``min_fill`` differs by surface on purpose. A product-page strip with
one item looks broken, so it wants two; a cart cross-sell of one
complementary item is a legitimate "add this one thing", so it wants
one — and a store with three products would otherwise never show a
cart suggestion at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from recommendation.enum import StrategyCode as S
from recommendation.enum import Surface
from tenant.models import StoreVertical

if TYPE_CHECKING:
    from recommendation.models import RecommendationSlot

_FREE_CHAIN = [S.CURATED, S.VARIANT_GROUP, S.CATEGORY, S.POPULAR]

# The slot fields a preset owns. ``reset_slot_to_preset`` writes exactly
# these; anything else on the row (timestamps, the surface) is not the
# preset's to touch.
PRESET_FIELDS = (
    "strategy_chain",
    "weights",
    "limit",
    "min_fill",
    "price_band_ratio",
    "enabled",
)


def _slot(
    chain: Sequence[str],
    *,
    limit: int = 4,
    min_fill: int = 2,
    price_band_ratio: str | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "strategy_chain": [str(code) for code in chain],
        "weights": weights or {},
        "limit": limit,
        "min_fill": min_fill,
        "price_band_ratio": price_band_ratio,
        "enabled": True,
    }


def _preset(
    *,
    pdp: Sequence[str],
    cart: Sequence[str],
    out_of_stock: Sequence[str],
    order_email: Sequence[str],
    cart_band: str,
    cart_limit: int = 4,
) -> dict[str, dict[str, Any]]:
    return {
        Surface.PDP: _slot(pdp),
        # The cart is about completing the order: one complementary
        # item is a real suggestion, so min_fill is 1.
        Surface.CART: _slot(
            cart, limit=cart_limit, min_fill=1, price_band_ratio=cart_band
        ),
        # A dead end: anything relevant beats nothing.
        Surface.OUT_OF_STOCK: _slot(out_of_stock, min_fill=1),
        Surface.EMPTY_CART: _slot([S.POPULAR], limit=6, min_fill=3),
        Surface.ORDER_EMAIL: _slot(order_email, limit=3),
    }


PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    StoreVertical.GENERAL: _preset(
        pdp=[
            S.CURATED,
            S.VARIANT_GROUP,
            S.ATTRIBUTES,
            S.SEMANTIC,
            S.CATEGORY,
            S.POPULAR,
        ],
        cart=[S.CURATED, S.CO_PURCHASE, S.ATTRIBUTES, S.CATEGORY, S.POPULAR],
        out_of_stock=[
            S.CURATED,
            S.VARIANT_GROUP,
            S.SEMANTIC,
            S.ATTRIBUTES,
            S.CATEGORY,
            S.POPULAR,
        ],
        order_email=[S.CURATED, S.CO_PURCHASE, S.ATTRIBUTES, S.CATEGORY],
        cart_band="3.00",
    ),
    # Fashion: the same garment in other colours is the first thing a
    # shopper wants; then the look it goes with.
    StoreVertical.FASHION: _preset(
        pdp=[
            S.VARIANT_GROUP,
            S.CURATED,
            S.ATTRIBUTES,
            S.SEMANTIC,
            S.CATEGORY,
            S.POPULAR,
        ],
        cart=[S.CURATED, S.CO_PURCHASE, S.SEMANTIC, S.ATTRIBUTES, S.POPULAR],
        out_of_stock=[
            S.VARIANT_GROUP,
            S.CURATED,
            S.SEMANTIC,
            S.ATTRIBUTES,
            S.CATEGORY,
            S.POPULAR,
        ],
        order_email=[S.CURATED, S.CO_PURCHASE, S.SEMANTIC],
        cart_band="4.00",
    ),
    # Plants & garden: complements (pot, soil, feed) matter more than
    # look-alikes, so curated leads everywhere and category is last.
    StoreVertical.PLANTS_GARDEN: _preset(
        pdp=[
            S.CURATED,
            S.CO_PURCHASE,
            S.ATTRIBUTES,
            S.SEMANTIC,
            S.CATEGORY,
            S.POPULAR,
        ],
        cart=[S.CURATED, S.CO_PURCHASE, S.ATTRIBUTES, S.POPULAR],
        out_of_stock=[
            S.CURATED,
            S.SEMANTIC,
            S.ATTRIBUTES,
            S.CATEGORY,
            S.POPULAR,
        ],
        order_email=[S.CURATED, S.CO_PURCHASE],
        cart_band="5.00",
    ),
    # Electronics: accessories and compatibility are curated facts;
    # semantic similarity is strong on spec-heavy descriptions.
    StoreVertical.ELECTRONICS: _preset(
        pdp=[
            S.CURATED,
            S.SEMANTIC,
            S.ATTRIBUTES,
            S.VARIANT_GROUP,
            S.CATEGORY,
            S.POPULAR,
        ],
        cart=[S.CURATED, S.CO_PURCHASE, S.ATTRIBUTES, S.POPULAR],
        out_of_stock=[
            S.CURATED,
            S.SEMANTIC,
            S.ATTRIBUTES,
            S.VARIANT_GROUP,
            S.CATEGORY,
        ],
        order_email=[S.CURATED, S.CO_PURCHASE],
        cart_band="2.00",
    ),
    # Food & drink: pairs-with is curated; repeat purchase dominates,
    # and baskets are bigger, so the cart strip is wider.
    StoreVertical.FOOD: _preset(
        pdp=[S.CURATED, S.CO_PURCHASE, S.CATEGORY, S.SEMANTIC, S.POPULAR],
        cart=[S.CURATED, S.CO_PURCHASE, S.CATEGORY, S.POPULAR],
        out_of_stock=[S.CURATED, S.CATEGORY, S.SEMANTIC, S.POPULAR],
        order_email=[S.CURATED, S.CO_PURCHASE],
        cart_band="6.00",
        cart_limit=6,
    ),
}


def slot_defaults(surface: str, vertical: str) -> dict[str, Any]:
    """The vertical's configuration for one surface, as model kwargs.

    Every ``StoreVertical`` has a preset (pinned by a test), so an
    unknown vertical is a programming error, not a runtime case; an
    unknown surface is the one thing an old preset can lack, and it gets
    the Free-tier chain rather than an exception on a product page.
    """
    try:
        return dict(PRESETS[vertical][surface])
    except KeyError:
        return _slot(_FREE_CHAIN)


def seed_recommendation_slots(vertical: str) -> int:
    """Create any missing slot rows from ``vertical``; return how many.

    Runs inside the tenant's schema context. Never overwrites: a slot
    the merchant has edited is theirs — ``reset_slot_to_preset`` is the
    explicit way back.
    """
    from recommendation.models import RecommendationSlot

    created = 0
    for surface in Surface.values:
        _, was_created = RecommendationSlot.objects.get_or_create(
            surface=surface, defaults=slot_defaults(surface, vertical)
        )
        created += int(was_created)
    return created


def reset_slot_to_preset(slot: RecommendationSlot, vertical: str) -> None:
    """Overwrite one slot's configuration with the vertical's preset.

    The merchant-facing way to undo edits (the slot admin's "Reset to
    preset"); ``clean()`` runs so a preset can never store what the
    admin form would refuse.
    """
    for field, value in slot_defaults(slot.surface, vertical).items():
        setattr(slot, field, value)
    slot.clean()
    slot.save(update_fields=[*PRESET_FIELDS, "updated_at"])
