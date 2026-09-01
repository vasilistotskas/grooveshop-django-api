from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface, register_surface


def _nuxt(*names: str) -> tuple[str, ...]:
    """Build Nuxt Nitro handler patterns from EXACT handler names.

    Nitro stores cached event handlers under
    ``cache:nitro:handlers:<name>:<key>``. The name must be complete:
    the purge endpoint resolves a trailing-``*`` pattern with unstorage's
    ``getKeys()``, which treats its argument as a key PATH and only
    matches on ``:`` segment boundaries. Use ``_nuxt_matching`` to sweep a
    family by name fragment.
    """

    return tuple(f"cache:nitro:handlers:{name}*" for name in names)


def _nuxt_matching(*fragments: str) -> tuple[str, ...]:
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


def _nuxt_routes(*paths: str) -> tuple[str, ...]:
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
    segment-boundary reason as ``_nuxt_matching``: the key's segment is
    the full ``blogpost42mnhmhr.<hash>``, so only a leading ``*`` gets the
    regex post-filter applied over the ``nitro:routes:_:`` prefix.

    This differs from ``_nuxt`` above, which targets cached API handlers
    (``cache:nitro:handlers:``). Purging a handler drops the JSON the page
    is built from; purging a route drops the rendered HTML. A merchant
    edit needs BOTH, or the storefront keeps serving the old page from
    Nitro's SSR cache until its TTL expires.
    """

    return tuple(
        "cache:nitro:routes:_:*{}*".format(
            "".join(ch for ch in path if ch.isalnum())[:16]
        )
        for path in paths
    )


def _nuxt_functions(*names: str) -> tuple[str, ...]:
    """Build Nuxt patterns for cached FUNCTIONS (``defineCachedFunction``).

    Stored as ``cache:nitro:functions:<name>:<args>``. Distinct from both
    ``_nuxt`` (cached event handlers) and ``_nuxt_routes`` (cached page
    renders) — the sitemap and RSS feeds build their data through cached
    functions, so neither of the other two prefixes ever matched them.
    """

    return tuple(f"cache:nitro:functions:{name}*" for name in names)


def register_default_surfaces() -> None:
    """Register the surfaces shipped by ``core``.

    Each app may register its own surfaces in ``AppConfig.ready()``;
    this function exists so the core surfaces are colocated and easy
    to audit in one place.
    """

    register_surface(
        CacheSurface(
            code="pay_way",
            label=_("Payment methods"),
            description=_(
                "Payment-method list and detail responses, plus the"
                " checkout sidebar that displays them. Purge after"
                " enabling/disabling a PayWay or changing its cost."
            ),
            django_patterns=("*PayWayViewSet_*",),
            nuxt_patterns=_nuxt("PayWayViewSet"),
            icon="payments",
            group="commerce",
        )
    )

    register_surface(
        CacheSurface(
            code="shipping",
            label=_("Shipping options"),
            description=_(
                "Shipping option matrix, locker pickers and the ACS"
                " station map. Purge after toggling a provider, editing"
                " a price, or after a station/locker sync."
            ),
            django_patterns=(
                "*BoxNowLockerViewSet_*",
                "*AcsStationViewSet_*",
            ),
            nuxt_patterns=_nuxt(
                "shipping.options",
                "shipping.lockers",
                "shipping.acs.nearest",
                "shipping.acs.stations",
            ),
            icon="local_shipping",
            group="commerce",
        )
    )

    register_surface(
        CacheSurface(
            code="products",
            label=_("Products"),
            description=_(
                "Product list/detail, images, attributes, related"
                " products, and the search endpoint."
            ),
            # Django ViewSet class names (used by ``cache_methods``):
            # ``AttributeViewSet`` and ``AttributeValueViewSet`` are
            # NOT prefixed with ``Product`` even though their Nuxt
            # handler names are. ``ProductImageViewSet`` is singular
            # in Django, plural (``ProductImagesViewSet``) in the Nuxt
            # /products/[id]/images route. Both are listed below.
            django_patterns=(
                "*ProductImageViewSet_*",
                "*ProductCategoryImageViewSet_*",
                "*AttributeViewSet_*",
                "*AttributeValueViewSet_*",
            ),
            nuxt_patterns=_nuxt(
                "ProductViewSet",
                "ProductDetailViewSet",
                "ProductImageViewSet",
                "ProductImagesViewSet",
                "ProductImageDetail",
                "ProductReviewsViewSet",
                "ProductTagsViewSet",
                "ProductAttributeViewSet",
                "ProductAttributeValueViewSet",
                "SearchProductViewSet",
            )
            # The rendered pages too, not just the JSON behind them:
            # /products and /products/** are SWR-cached (300s), so
            # without this a price edit stayed visible for the whole TTL.
            + _nuxt_routes("/products"),
            related=("categories", "tags"),
            icon="inventory_2",
            group="catalog",
        )
    )

    register_surface(
        CacheSurface(
            code="categories",
            label=_("Categories"),
            description=_(
                "Category trees, listing pages and the category map"
                " used for the navbar."
            ),
            django_patterns=("*ProductCategoryViewSet_*",),
            nuxt_patterns=_nuxt(
                "ProductCategoryViewSet",
                "ProductCategoryAll",
                "ProductCategoryDetail",
            )
            # Category pages live under /products/category/**, which the
            # same escaped-path prefix covers.
            + _nuxt_routes("/products"),
            icon="category",
            group="catalog",
        )
    )

    register_surface(
        CacheSurface(
            code="blog",
            label=_("Blog"),
            description=_(
                "Blog post list/detail, related posts, comments,"
                " categories, tags, and authors."
            ),
            django_patterns=(
                "*BlogPostViewSet_*",
                "*BlogCategoryViewSet_*",
                "*BlogTagViewSet_*",
                "*BlogAuthorViewSet_*",
            ),
            # ``cache:nitro:handlers:Blog*`` prefix-matches every Nuxt
            # blog handler (BlogPostViewSet, BlogPostDetailViewSet,
            # BlogPostComments, BlogCategoryViewSet, BlogCategoryDetail,
            # BlogCategoryPostsViewSet, BlogTagViewSet, BlogTagDetail,
            # BlogAuthorViewSet, BlogAuthorDetail, BlogCommentViewSet).
            # ``_nuxt_matching("Blog")``, not ``_nuxt("Blog")``: the
            # latter matched zero keys (see the helper docstrings).
            # ``_nuxt_routes("/blog")`` adds the rendered pages —
            # /blog, /blog/categories, /blog/category/**, /blog/post/**
            # are SWR-cached (600s).
            nuxt_patterns=_nuxt_matching("Blog") + _nuxt_routes("/blog"),
            icon="article",
            group="content",
        )
    )

    register_surface(
        CacheSurface(
            code="regions_countries",
            label=_("Regions & Countries"),
            description=_(
                "Country and region pickers used during checkout"
                " address selection."
            ),
            django_patterns=("*RegionViewSet_*", "*CountryViewSet_*"),
            nuxt_patterns=_nuxt("RegionViewSet", "CountryViewSet"),
            icon="public",
            group="commerce",
        )
    )

    register_surface(
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
            nuxt_patterns=_nuxt(
                "loyalty-settings",
                "LoyaltySummaryAnon",
            ),
            icon="loyalty",
            group="commerce",
        )
    )

    register_surface(
        CacheSurface(
            code="promotions",
            label=_("Promotions"),
            description=_(
                "The public offers listing. An offer is a commercial"
                " commitment with an end date, so a stale entry"
                " advertises a discount the cart will refuse — purge"
                " after editing a promotion, its codes, or its"
                " schedule."
            ),
            # ``PublicPromotionListView`` is a plain APIView with no
            # ``@cache_methods`` decorator, so there is no Django-side
            # response cache to purge — only the Nuxt handler and the
            # rendered /offers page.
            django_patterns=(),
            nuxt_patterns=_nuxt("PublicPromotionList")
            + _nuxt_routes("/offers"),
            icon="local_offer",
            group="commerce",
        )
    )

    register_surface(
        CacheSurface(
            code="tags",
            label=_("Tags"),
            description=_(
                "Generic Tag list/detail and the M2M-through"
                " TaggedItem cache. Tags are shared by Products and"
                " Blog posts so this surface lives outside both."
            ),
            django_patterns=("*TagViewSet_*", "*TaggedItemViewSet_*"),
            nuxt_patterns=(),
            icon="sell",
            group="content",
        )
    )

    register_surface(
        CacheSurface(
            code="settings",
            label=_("Site settings"),
            description=_(
                "django-extra-settings cache (extra_settings_*) and the"
                " admin dashboard summary. Touches the Nuxt"
                " /api/settings proxy and the published seller identity."
            ),
            django_patterns=(
                "*extra_settings_*",
                "*admin:dashboard*",
                "*SettingsViewSet_*",
            ),
            # ``tenantLegalIdentity`` is the storefront's published seller
            # identity, and it is built from the INVOICE_SELLER_* rows in
            # extra_settings — so editing them here has to invalidate it.
            # Without this entry the footer keeps serving the previous
            # identity for the route's 30-minute maxAge, which for a
            # merchant who just corrected their GEMI number or address
            # means the wrong legal identity stays published.
            nuxt_patterns=_nuxt("settings", "tenantLegalIdentity"),
            related=("pay_way", "shipping", "loyalty"),
            icon="tune",
            group="config",
        )
    )

    register_surface(
        CacheSurface(
            code="sitemap_seo",
            label=_("Sitemap & SEO"),
            description=_(
                "XML sitemap, RSS, llms.txt and the OG image cache."
                " Cheap to regenerate; purge after publishing"
                " content that should appear immediately."
            ),
            django_patterns=(),
            # These are cached FUNCTIONS, not routes: the sitemap source
            # and the RSS feed build their data through
            # ``defineCachedFunction`` (``sitemap:blog-posts``,
            # ``rss:products``, ``RssFeed``, ...), so the previous
            # ``cache:nitro:routes:__sitemap__*`` / ``:rss*`` patterns
            # matched no key in Redis and this surface's Nuxt half was a
            # no-op. Verified against the live keyspace.
            nuxt_patterns=_nuxt_functions("sitemap", "rss", "RssFeed")
            + _nuxt("nuxt-ai-ready"),
            icon="map",
            group="content",
        )
    )

    register_surface(
        CacheSurface(
            code="translations",
            label=_("Translations (parler)"),
            description=_(
                "Per-row parler translation cache. Purging this"
                " forces a re-fetch from Postgres on the next request"
                " for every translatable row touched. Use sparingly."
            ),
            django_patterns=("*parler.*",),
            nuxt_patterns=(),
            icon="translate",
            group="config",
            danger=True,
        )
    )
