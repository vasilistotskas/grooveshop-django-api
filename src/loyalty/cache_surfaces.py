"""The loyalty app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import nuxt_handlers, nuxt_routes

SURFACES = (
    CacheSurface(
        code="loyalty",
        label=_("Loyalty"),
        description=_(
            "Loyalty settings, tiers, and summaries surfaced on"
            " the storefront. Backend Loyalty ViewSets are not"
            " ``@cache_methods``-decorated so this purge only hits"
            " the Nuxt SSR cache."
        ),
        # Django side: Loyalty ViewSets are not ``cache_methods``-
        # decorated. Pattern intentionally omitted.
        django_patterns=(),
        nuxt_patterns=nuxt_handlers(
            "loyalty-settings",
            "LoyaltySummaryAnon",
        )
        # The cached programme page renders these settings.
        + nuxt_routes("/loyalty-program"),
        icon="loyalty",
        group="commerce",
    ),
)
