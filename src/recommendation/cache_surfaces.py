"""The recommendation app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import SALE_UPDATE_FIELDS, nuxt_handlers

SURFACES = (
    CacheSurface(
        code="recommendations",
        label=_("Product recommendations"),
        description=_(
            "Suggested-product responses and the cached tenant facts"
            " the strategies consult. Purges itself when a product,"
            " a curated relation or a slot's configuration changes,"
            " so a merchant's edit shows on the next request."
        ),
        # No ``*ViewSet_*`` pattern: the read endpoint is a function
        # view that is deliberately NOT ``cache_page``d (every
        # response mints its own impression id). What lives in
        # Django's cache is the per-tenant context under ``recs:``.
        django_patterns=("*recs:*",),
        nuxt_patterns=nuxt_handlers("productRecommendations"),
        # Every OPERATOR write that changes what a strategy would
        # answer. ``RecommendationCandidate`` is deliberately absent:
        # its rows are rebuilt by queryset delete + bulk_create, and
        # a post_delete receiver would make Django instantiate and
        # signal every row — one purge per product across a nightly
        # full pass. A per-product rebuild is already covered by the
        # Product/ProductRelation write that triggered it; the full
        # pass purges once, explicitly, when it finishes
        # (``recommendation.tasks.recompute_all_candidates``).
        invalidated_by=(
            "product.Product",
            "product.ProductCategory",
            "product.ProductRelation",
            "recommendation.RecommendationSlot",
        ),
        # The engine reads stock live (its guard drops sold-out
        # products), so only the storefront's hydrated strips can show
        # an old stock figure ("only 2 left"), bounded by their TTL like
        # the listing's cards. A sale does not purge them; a product
        # that sells out or comes back does.
        ignored_update_fields=SALE_UPDATE_FIELDS,
        icon="recommend",
        group="commerce",
    ),
)
