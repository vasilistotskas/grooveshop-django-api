from __future__ import annotations

from importlib import import_module

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import module_has_submodule
from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface, register_surface

#: Where Nitro keeps its cached page renders (``nuxt_routes``).
NUXT_ROUTE_PREFIX = "cache:nitro:routes:"

#: Every page the storefront has cached for a store, and its settings read.
STORE_PAGES_SURFACE = "store_pages"

#: The sitemap, RSS, llms.txt and OG image caches.
SITEMAP_SEO_SURFACE = "sitemap_seo"

#: The fields a sale saves (``order/inventory/stock.py``). A save of
#: nothing else is bookkeeping for the surfaces that name this set, except a
#: product that sells out or comes back (``product.availability``).
SALE_UPDATE_FIELDS = frozenset({"stock", "updated_at"})


def nuxt_handlers(*names: str) -> tuple[str, ...]:
    """Build Nuxt Nitro handler patterns from EXACT handler names.

    Nitro stores cached event handlers under
    ``cache:nitro:handlers:<name>:<key>``. The name must be complete:
    the purge endpoint resolves a trailing-``*`` pattern with unstorage's
    ``getKeys()``, which treats its argument as a key PATH and only
    matches on ``:`` segment boundaries. Use ``nuxt_handlers_matching``
    to sweep a family by name fragment.
    """

    return tuple(f"cache:nitro:handlers:{name}*" for name in names)


def nuxt_handlers_matching(*fragments: str) -> tuple[str, ...]:
    """Build handler patterns that match a NAME FRAGMENT.

    ``getKeys("nitro:handlers:Blog")`` returns nothing, because the key's
    segment is the whole handler name (``BlogCategoryDetail``), not a
    prefix of it — so ``cache:nitro:handlers:Blog*`` silently matched
    zero keys while claiming to cover every blog handler. A LEADING
    ``*`` makes the purge endpoint fall back to its regex post-filter
    over the ``nitro:handlers:`` prefix instead, which is the only shape
    that can sweep a family.

    Measured against production on 2026-08-29:
    ``handlers:Blog*`` -> 0 keys, ``handlers:*Blog*`` -> 270.
    """

    return tuple(f"cache:nitro:handlers:*{fragment}*" for fragment in fragments)


def nuxt_routes(*paths: str) -> tuple[str, ...]:
    """Build Nuxt patterns for CACHED SSR PAGES (Nitro route rules).

    Nitro keys a cached route as
    ``cache:nitro:routes:_:<pathname>.<hash>:<vary>.<hash>...`` where
    ``<pathname>`` is the URL path with every non-word character removed,
    truncated to 16 chars, and ``_`` is the unnamed cached function. So
    ``/blog/post/42/x`` is stored under ``_:blogpost42x...``.

    Callers therefore pass a URL PATH and it is flattened the same way —
    ``/blog`` covers every blog page because each one's escaped pathname
    starts with ``blog``.

    The pattern is ``_:*<path>*`` rather than ``_:<path>*`` for the same
    segment-boundary reason as ``nuxt_handlers_matching``: the key's segment is
    the full ``blogpost42mnhmhr.<hash>``, so only a leading ``*`` gets the
    regex post-filter applied over the ``nitro:routes:_:`` prefix.

    This differs from ``nuxt_handlers`` above, which targets cached API handlers
    (``cache:nitro:handlers:``). Purging a handler drops the JSON the page
    is built from; purging a route drops the rendered HTML. A merchant
    edit needs BOTH, or the storefront keeps serving the old page from
    Nitro's SSR cache until its TTL expires.
    """

    return tuple(
        f"{NUXT_ROUTE_PREFIX}_:*{_escaped_pathname(path)}*" for path in paths
    )


def _escaped_pathname(path: str) -> str:
    """Nitro's own escaping of a URL path, for key matching.

    Non-word characters are stripped and the result truncated to 16
    chars. The ONE special case is the site root: Nitro stores "/" under
    the literal segment ``index``, not an empty string — verified in the
    live keyspace as
    ``cache:nitro:routes:_:index.<hash>:host.<hash>:xdeviceclass.<hash>.json``.

    Without this branch, ``nuxt_routes("/")`` returned
    ``routes:_:**``, which matches EVERY cached page render — so a
    caller wanting to drop the homepage silently dropped the whole
    site's SSR cache instead.
    """
    if path in ("", "/"):
        return "index"
    return "".join(ch for ch in path if ch.isalnum())[:16]


def nuxt_functions(*names: str) -> tuple[str, ...]:
    """Build Nuxt patterns for cached FUNCTIONS (``defineCachedFunction``).

    Stored as ``cache:nitro:functions:<name>:<args>``. Distinct from both
    ``nuxt_handlers`` (cached event handlers) and ``nuxt_routes`` (cached page
    renders) — the sitemap and RSS feeds build their data through cached
    functions, so neither of the other two prefixes ever matched them.
    """

    return tuple(f"cache:nitro:functions:{name}*" for name in names)


#: The surfaces no single app owns: the settings, every storefront page,
#: and the sitemap and feeds.
CORE_SURFACES = (
    CacheSurface(
        code="settings",
        label=_("Site settings"),
        description=_(
            "django-extra-settings cache (extra_settings_*) and the"
            " admin dashboard summary. Touches the Nuxt"
            " /api/settings proxy and the published seller identity."
        ),
        django_patterns=(
            # django-extra-settings' own cache: cache.py builds its
            # key as f"extra_settings_{name}", so this matches one
            # key per Setting row (92 on a provisioned tenant).
            "*extra_settings_*",
            "*admin:dashboard*",
            # NOTE: a "*SettingsViewSet_*" pattern used to sit here
            # and matched nothing — there is no such class. The
            # settings API is two plain @api_view functions
            # (core.api.views.list_settings / get_setting_by_key)
            # with no cache_page or cache_methods, so it has no
            # Django response cache to purge. Do not re-add it:
            # a pattern that matches nothing makes the purge report
            # success while stale content keeps being served.
        ),
        # ``tenantLegalIdentity`` is the storefront's published seller
        # identity, and it is built from the INVOICE_SELLER_* rows in
        # extra_settings — so editing them here has to invalidate it.
        # Without this entry the footer keeps serving the previous
        # identity for the route's 30-minute maxAge, which for a
        # merchant who just corrected their GEMI number or address
        # means the wrong legal identity stays published.
        #
        # ``settingsPublic`` is the storefront's ONE settings read
        # (server/api/settings/public.get.ts, tenant-cached for a
        # minute). ``nuxt_handlers`` needs the EXACT handler name — the
        # ``"settings"`` that used to sit here matched no handler
        # (the route was ``settingsGet``), so purging this surface
        # never reached the storefront's settings cache.
        nuxt_patterns=nuxt_handlers("settingsPublic", "tenantLegalIdentity"),
        # Every store setting lives in an ``extra_settings.Setting``
        # row: a save from the admin, a shell or a migration has to
        # purge the caches above, or the storefront keeps the old
        # settings and seller identity for the routes' lifetime.
        invalidated_by=("extra_settings.Setting",),
        invalidates_tenant_config=True,
        related=("pay_way", "shipping", "loyalty"),
        icon="tune",
        group="config",
    ),
    CacheSurface(
        code=STORE_PAGES_SURFACE,
        label=_("Storefront pages"),
        description=_(
            "Every page the storefront has rendered and cached for"
            " this store, and its settings read. Purges itself when a"
            " store feature is switched on or off, because the"
            " navigation, footer and product cards of every page show"
            " what the store offers."
        ),
        django_patterns=(),
        # The settings read first, so a page rendered between the
        # two deletions already reads the new switch. Then every
        # rendered page: one pattern for the whole route family,
        # which the purge endpoint narrows to this store's keys
        # (``host``). Purging route keys also queues the edge purge.
        nuxt_patterns=nuxt_handlers("settingsPublic")
        + (f"{NUXT_ROUTE_PREFIX}_:*",),
        invalidates_tenant_config=True,
        icon="web",
        group="content",
    ),
    CacheSurface(
        code=SITEMAP_SEO_SURFACE,
        label=_("Sitemap & SEO"),
        description=_(
            "XML sitemap, RSS, llms.txt and the OG image cache."
            " Purged when a post, product, category or content page"
            " changes or goes live."
        ),
        django_patterns=(),
        # These are cached FUNCTIONS, not routes: the sitemap source
        # and the RSS feed build their data through
        # ``defineCachedFunction`` (``sitemap:blog-posts``,
        # ``rss:feed``, ``rss:blog-posts``, ...), so the previous
        # ``cache:nitro:routes:__sitemap__*`` / ``:rss*`` patterns
        # matched no key in Redis and this surface's Nuxt half was a
        # no-op. Verified against the live keyspace.
        #
        # The sitemap ITSELF is a route-rule cache (``/sitemap.xml``,
        # keyed per host): @nuxtjs/sitemap's own cache lived in each
        # pod's memory, where no purge reached it, so the storefront
        # turned it off and caches the response in Redis instead.
        #
        # Every read the feeds make is a function named ``rss:<what>``
        # (the storefront's ``server/utils/rssFeed.ts``), so ``rss``
        # purges the feed and the posts and categories it was built
        # from.
        nuxt_patterns=nuxt_functions("sitemap", "rss")
        + nuxt_routes("/sitemap.xml")
        + nuxt_handlers("nuxt-ai-ready"),
        # What the sitemap and the feeds list. A row that goes live at
        # its publish date is reported by ``core.publication``.
        invalidated_by=(
            "blog.BlogPost",
            "blog.BlogPostTranslation",
            "blog.BlogCategory",
            "blog.BlogCategoryTranslation",
            "product.Product",
            "product.ProductTranslation",
            "product.ProductCategory",
            "product.ProductCategoryTranslation",
            "page_config.ContentPage",
            "page_config.ContentPageTranslation",
        ),
        # Each order's stock decrement, as on the products surface.
        ignored_update_fields=SALE_UPDATE_FIELDS,
        icon="map",
        group="content",
    ),
)


def register_default_surfaces() -> None:
    """Register core's surfaces, then every app's.

    An app declares its surfaces as ``SURFACES`` in its own
    ``cache_surfaces`` module; this collects them, so the whole set is
    registered before ``connect_surface_invalidation`` reads it and can be
    registered again after a test empties the registry. Two modules
    declaring the same code stop the boot: the registry would keep the
    later one alone, and wire its ``invalidated_by`` in silence.
    """

    sources = [(__name__, CORE_SURFACES)]
    for app_config in apps.get_app_configs():
        if module_has_submodule(app_config.module, "cache_surfaces"):
            module = import_module(f"{app_config.name}.cache_surfaces")
            sources.append((module.__name__, module.SURFACES))

    declared_by: dict[str, str] = {}
    for origin, surfaces in sources:
        for surface in surfaces:
            if surface.code in declared_by:
                raise ImproperlyConfigured(
                    f"Cache surface {surface.code!r} is declared by both "
                    f"{declared_by[surface.code]} and {origin}"
                )
            declared_by[surface.code] = origin
            register_surface(surface)
