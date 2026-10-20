"""Shared attribute values, tags and brand — Jaccard similarity over
each product's set of those features.

A product's features are its active attribute values, its active tags
and its brand. Two products score ``|A ∩ B| / |A ∪ B|``: "same
material, same colour, same brand" outranks "same brand". It declines
when the store has populated none of the three, because then every
product's set is empty and every score is zero.

Precomputed: the answer is a set intersection over the catalogue, which
is not one cheap query at 100k SKUs. ``compute_candidates`` writes it to
``RecommendationCandidate`` on product save and in the nightly pass.

A feature carried by more than
``RECOMMENDATION_ATTRIBUTES_MAX_FEATURE_PRODUCTS`` active products is not
used to FIND candidates. It is what bounds the work: a brand or a colour
on half of a large catalogue would otherwise make every product a
candidate of every other, and it says almost nothing about similarity
there anyway. It still counts in each product's set size, so the score
stays the true Jaccard over everything both products carry. A small
store, where every feature is under the cap, uses all of them.

Fixed query count per batch of seeds, whatever the batch size.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from django.conf import settings
from django.db.models import Count

from recommendation.enum import StrategyCode
from recommendation.strategies.base import (
    Candidate,
    RecommendationStrategy,
    TenantContext,
    register_strategy,
)
from tenant.models import TenantPlan

# One feature: its kind and the id of the attribute value, tag or brand.
type Feature = tuple[str, int]

_VALUE = "value"
_TAG = "tag"
_BRAND = "brand"


@register_strategy
class AttributesStrategy(RecommendationStrategy):
    code = StrategyCode.ATTRIBUTES
    label = StrategyCode.ATTRIBUTES.label
    min_plan = TenantPlan.BASIC
    precompute = True

    def is_available(self, ctx: TenantContext) -> bool:
        if not super().is_available(ctx):
            return False
        return ctx.has_attributes or ctx.has_tags or ctx.has_brands

    def candidates(self, product_id: int, limit: int) -> list[Candidate]:
        return self.candidates_for([product_id], limit).get(product_id, [])

    def candidates_for(
        self, seed_ids: Sequence[int], limit: int
    ) -> dict[int, list[Candidate]]:
        seeds = list(seed_ids)
        seed_features = _features_of(seeds)
        if not seed_features:
            return {}

        wanted: set[Feature] = set().union(*seed_features.values())
        informative = _informative(wanted)
        holders = _holders(informative)

        shared: dict[int, dict[int, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for seed_id, features in seed_features.items():
            for feature in features & informative:
                for product_id in holders.get(feature, ()):
                    if product_id != seed_id:
                        shared[seed_id][product_id] += 1

        candidate_ids = {
            pid for per_seed in shared.values() for pid in per_seed
        }
        sizes = _feature_counts(candidate_ids)

        out: dict[int, list[Candidate]] = {}
        for seed_id, per_seed in shared.items():
            seed_size = len(seed_features[seed_id])
            scored = sorted(
                (
                    (
                        overlap
                        / (seed_size + sizes.get(product_id, 0) - overlap),
                        product_id,
                    )
                    for product_id, overlap in per_seed.items()
                ),
                key=lambda pair: (-pair[0], pair[1]),
            )[:limit]
            out[seed_id] = [
                Candidate(product_id=product_id, score=round(score, 4))
                for score, product_id in scored
            ]
        return out


def _product_tags():
    from product.models import Product
    from tag.models.tagged_item import TaggedItem

    # Joined rather than ``ContentType.objects.get_for_model``, for the
    # reason ``recommendation/context.py`` gives: a constant query count.
    opts = Product._meta
    return TaggedItem.objects.filter(
        content_type__app_label=opts.app_label,
        content_type__model=opts.model_name,
        tag__active=True,
    )


def _product_values():
    from product.models import ProductAttribute

    return ProductAttribute.objects.filter(
        attribute_value__active=True,
        attribute_value__attribute__active=True,
    )


def _features_of(product_ids: Sequence[int]) -> dict[int, set[Feature]]:
    """Each product's full feature set; products with none are absent."""
    from product.models import Product

    features: dict[int, set[Feature]] = defaultdict(set)
    for product_id, value_id in (
        _product_values()
        .filter(product_id__in=product_ids)
        .values_list("product_id", "attribute_value_id")
    ):
        features[product_id].add((_VALUE, value_id))
    for product_id, tag_id in (
        _product_tags()
        .filter(object_id__in=product_ids)
        .values_list("object_id", "tag_id")
    ):
        features[product_id].add((_TAG, tag_id))
    for product_id, brand_id in Product.objects.filter(
        id__in=product_ids, brand_id__isnull=False
    ).values_list("id", "brand_id"):
        features[product_id].add((_BRAND, brand_id))
    return dict(features)


def _ids(features: set[Feature], kind: str) -> list[int]:
    return [
        feature_id
        for feature_kind, feature_id in features
        if feature_kind == kind
    ]


def _informative(features: set[Feature]) -> set[Feature]:
    """The features carried by few enough active products to be worth
    looking candidates up by."""
    from product.models import Product

    cap = settings.RECOMMENDATION_ATTRIBUTES_MAX_FEATURE_PRODUCTS
    counts: dict[Feature, int] = {}
    for value_id, n in (
        _product_values()
        .filter(
            attribute_value_id__in=_ids(features, _VALUE),
            product__active=True,
            product__is_deleted=False,
        )
        .values("attribute_value_id")
        .annotate(n=Count("product_id", distinct=True))
        .values_list("attribute_value_id", "n")
    ):
        counts[(_VALUE, value_id)] = n
    active_products = Product.objects.active().values("id")
    for tag_id, n in (
        _product_tags()
        .filter(tag_id__in=_ids(features, _TAG), object_id__in=active_products)
        .values("tag_id")
        .annotate(n=Count("object_id", distinct=True))
        .values_list("tag_id", "n")
    ):
        counts[(_TAG, tag_id)] = n
    for brand_id, n in (
        Product.objects.active()
        .filter(brand_id__in=_ids(features, _BRAND))
        .values("brand_id")
        .annotate(n=Count("id"))
        .values_list("brand_id", "n")
    ):
        counts[(_BRAND, brand_id)] = n
    return {feature for feature, n in counts.items() if n <= cap}


def _holders(features: set[Feature]) -> dict[Feature, list[int]]:
    """The active products carrying each feature."""
    from product.models import Product

    holders: dict[Feature, list[int]] = defaultdict(list)
    value_ids = _ids(features, _VALUE)
    tag_ids = _ids(features, _TAG)
    brand_ids = _ids(features, _BRAND)
    if value_ids:
        for product_id, value_id in (
            _product_values()
            .filter(
                attribute_value_id__in=value_ids,
                product__active=True,
                product__is_deleted=False,
            )
            .values_list("product_id", "attribute_value_id")
        ):
            holders[(_VALUE, value_id)].append(product_id)
    if tag_ids:
        for product_id, tag_id in (
            _product_tags()
            .filter(
                tag_id__in=tag_ids,
                object_id__in=Product.objects.active().values("id"),
            )
            .values_list("object_id", "tag_id")
        ):
            holders[(_TAG, tag_id)].append(product_id)
    if brand_ids:
        for product_id, brand_id in (
            Product.objects.active()
            .filter(brand_id__in=brand_ids)
            .values_list("id", "brand_id")
        ):
            holders[(_BRAND, brand_id)].append(product_id)
    return dict(holders)


def _feature_counts(product_ids: set[int]) -> dict[int, int]:
    """How many features each product carries — the ``|B|`` of the
    Jaccard denominator."""
    from product.models import Product

    if not product_ids:
        return {}
    counts: dict[int, int] = defaultdict(int)
    for product_id, n in (
        _product_values()
        .filter(product_id__in=product_ids)
        .values("product_id")
        .annotate(n=Count("id"))
        .values_list("product_id", "n")
    ):
        counts[product_id] += n
    for product_id, n in (
        _product_tags()
        .filter(object_id__in=product_ids)
        .values("object_id")
        .annotate(n=Count("id"))
        .values_list("object_id", "n")
    ):
        counts[product_id] += n
    for product_id in Product.objects.filter(
        id__in=product_ids, brand_id__isnull=False
    ).values_list("id", flat=True):
        counts[product_id] += 1
    return dict(counts)
