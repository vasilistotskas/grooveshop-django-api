"""The builder app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import NUXT_ROUTE_PREFIX, nuxt_functions, nuxt_handlers

SURFACES = (
    CacheSurface(
        code="builder_documents",
        label=_("Pages showing changed records"),
        description=_(
            "The rendered pages of the page builder that show a product,"
            " category, blog post or promotion that changed. Purges"
            " itself when one is saved or deleted."
        ),
        # No pattern: the storefront maps each document to its routes
        # (``builder.data_purge``).
        purges_builder_documents=True,
        icon="web",
        group="content",
    ),
    CacheSurface(
        code="builder",
        label=_("Page builder"),
        description=_(
            "The pages, theme and sections a store published with the"
            " page builder, and every page rendered with them. Purges"
            " itself when a release is published or rolled back; saving"
            " a draft purges nothing."
        ),
        # The public read is keyed by the release number
        # (``builder.published``), which a new release moves, so
        # Django has nothing to purge. The storefront's copy and the
        # rendered pages go, and the release moves the store's cache
        # generation (``tenant.signals``) so the storefront re-keys
        # every cached page of the store at once, as for
        # ``store_texts``.
        django_patterns=(),
        # ``builderPageDocument`` is the cached function that replaces
        # the ``builderPage`` handler; both are purged until the
        # storefront has dropped the handler.
        nuxt_patterns=nuxt_handlers("builderPage", "builderChrome")
        + nuxt_functions(
            "builderPageDocument", "builderDataTargets", "builderDataMedia"
        )
        + (f"{NUXT_ROUTE_PREFIX}_:*",),
        invalidates_tenant_config=True,
        invalidated_by=("builder.BuilderRelease",),
        icon="web",
        group="content",
    ),
)
