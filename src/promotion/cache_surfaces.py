"""The promotion app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import nuxt_functions, nuxt_handlers, nuxt_routes

SURFACES = (
    CacheSurface(
        code="promotions",
        label=_("Promotions"),
        description=_(
            "The public offers listing and the per-product offer"
            " panel. An offer is a commercial commitment with an"
            " end date, so a stale entry advertises a discount the"
            " cart will refuse — purge after editing a promotion,"
            " its codes, its scope or its schedule."
        ),
        # Both promotion views are plain APIViews with no
        # ``@cache_methods`` decorator, so there is no Django-side
        # response cache to purge — only the Nuxt handlers and the
        # rendered pages.
        django_patterns=(),
        # ``/products`` is here, not only in the ``products``
        # surface: the product page now RENDERS the offers that
        # apply to it, so a promotion edit that changes which
        # products it targets leaves every product page's SSR
        # cache advertising the old scope. Promotions are edited
        # rarely; a stale offer panel would survive the whole
        # SWR window.
        nuxt_patterns=nuxt_handlers(
            "PublicPromotionList", "ProductPromotionList"
        )
        + nuxt_functions("builderDataPromotion", "builderDataProduct")
        + nuxt_routes("/offers", "/products"),
        invalidated_by=(
            "promotion.Promotion",
            "promotion.PromotionTranslation",
            # The listing prints one of the promotion's codes.
            "promotion.PromotionCode",
        ),
        icon="local_offer",
        group="commerce",
    ),
)
