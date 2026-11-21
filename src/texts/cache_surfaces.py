"""The texts app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import NUXT_ROUTE_PREFIX, nuxt_handlers

SURFACES = (
    CacheSurface(
        code="store_texts",
        label=_("Storefront texts"),
        description=_(
            "The wording a store replaced in its storefront, and every"
            " page rendered with it. Purges itself when a text is saved,"
            " reset or restored in the text editor."
        ),
        # The overrides endpoint is keyed by the store's cache
        # generation (``texts.views``), which the save bumps, so
        # Django has nothing to purge. The storefront's own copy is
        # the handler below; the rendered pages carry the wording on
        # every route, so the whole route family goes, as for
        # ``store_pages``. The name is the storefront's
        # ``defineCachedEventHandler`` name for
        # ``server/api/texts/[locale].get.ts``.
        django_patterns=(),
        nuxt_patterns=nuxt_handlers("textOverrides")
        + (f"{NUXT_ROUTE_PREFIX}_:*",),
        invalidates_tenant_config=True,
        invalidated_by=("texts.TextOverride",),
        icon="translate",
        group="content",
    ),
)
