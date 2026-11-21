"""The page_config app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import NUXT_ROUTE_PREFIX, nuxt_functions, nuxt_handlers

SURFACES = (
    CacheSurface(
        code="page_config",
        label=_("Content pages"),
        description=_(
            "The content pages (policies, FAQ, info pages) and the"
            " RENDERED pages that show or link them. Purge after"
            " editing a content page."
        ),
        # NOTHING in page_config is ``@cache_methods``-decorated:
        # ContentPageViewSet — despite being a BaseModelViewSet —
        # carries no decorator (checked against page_config/views.py,
        # which does not import cache_methods at all). A
        # ``*ContentPageViewSet_*`` pattern
        # here would match zero keys and report success, which is
        # the exact failure test_surface_patterns.py exists to
        # prevent. So the Django half is empty by fact, not by
        # oversight, and this surface purges Nuxt only.
        django_patterns=(),
        # Two halves, and BOTH are needed: purging the handler drops
        # the JSON, purging the route drops the HTML already built
        # from it. Without the route half an edit sits behind
        # Nitro's SSR cache for the rest of its TTL.
        #
        # Every cached page of the store, not a list of paths: the
        # footers and the checkout's legal strip link content pages
        # from every page, and a custom ContentPage's path is
        # whatever the merchant typed. A fixed list left those stale
        # for up to 600 s. The purge endpoint narrows the one pattern
        # to this store's keys (``host``), as for ``store_pages``, and
        # a route-prefix pattern queues the edge purge.
        # These are ``defineCachedEventHandler`` NAMES from the Nuxt
        # repo, not guesses — verified against the ``name:`` field of
        # server/api/content-pages/*.get.ts. A name that matches no
        # handler purges nothing and reports success (an operator's
        # menu edit stayed invisible that way, staging 2026-09-01).
        # Pages and menus are the page builder's: its own surface.
        nuxt_patterns=nuxt_handlers(
            "ContentPageViewSet",
            "ContentPageDetailViewSet",
        )
        + nuxt_functions("builderDataTargets")
        + (f"{NUXT_ROUTE_PREFIX}_:*",),
        invalidated_by=(
            "page_config.ContentPage",
            "page_config.ContentPageTranslation",
        ),
        icon="dashboard_customize",
        group="content",
    ),
)
