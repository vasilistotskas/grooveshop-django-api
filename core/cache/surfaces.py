from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface, register_surface


def _nuxt(*names: str) -> tuple[str, ...]:
    """Build Nuxt Nitro handler patterns.

    Nitro stores cached event handlers under ``cache:nitro:handlers:<name>:*``.
    """

    return tuple(f"cache:nitro:handlers:{name}*" for name in names)


def _nuxt_routes(*names: str) -> tuple[str, ...]:
    return tuple(f"cache:nitro:routes:{name}*" for name in names)


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
            related=("orders",),
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
            related=("orders",),
            icon="local_shipping",
            group="commerce",
        )
    )

    register_surface(
        CacheSurface(
            code="orders",
            label=_("Order serializers"),
            description=_(
                "Cached order detail responses (which embed PayWay and"
                " shipping payloads). Cleared automatically when"
                " PayWay or Shipping is purged."
            ),
            django_patterns=("*OrderViewSet_*",),
            nuxt_patterns=(),
            icon="receipt_long",
            group="commerce",
        )
    )

    register_surface(
        CacheSurface(
            code="products",
            label=_("Products"),
            description=_(
                "Product list/detail, images, reviews, tags,"
                " attributes, related products, and the search endpoint."
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
                "*ProductReviewViewSet_*",
                "*AttributeViewSet_*",
                "*AttributeValueViewSet_*",
                "*TagViewSet_*",
                "*TaggedItemViewSet_*",
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
            ),
            related=("categories",),
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
            ),
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
            nuxt_patterns=_nuxt("Blog"),
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
                " the storefront."
            ),
            django_patterns=("*Loyalty*ViewSet_*",),
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
            code="settings",
            label=_("Site settings"),
            description=_(
                "django-extra-settings cache (extra_settings_*) and the"
                " admin dashboard summary. Touches the Nuxt"
                " /api/settings proxy too."
            ),
            django_patterns=(
                "*extra_settings_*",
                "*admin:dashboard*",
                "*SettingsViewSet_*",
            ),
            nuxt_patterns=_nuxt("settings"),
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
            nuxt_patterns=_nuxt_routes("__sitemap__", "rss")
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
