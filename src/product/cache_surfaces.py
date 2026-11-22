"""The product app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import (
    SALE_UPDATE_FIELDS,
    nuxt_functions,
    nuxt_handlers,
    nuxt_routes,
)
from shipping.cache_surfaces import (
    PRODUCT_SHIPPING_HANDLER,
    PRODUCT_SHIPPING_VIEW,
)

SURFACES = (
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
            "*BrandViewSet_*",
            # A product's price, weight and size move what shipping it
            # costs.
            PRODUCT_SHIPPING_VIEW,
        ),
        nuxt_patterns=nuxt_handlers(
            "ProductViewSet",
            "ProductDetailViewSet",
            "ProductImageViewSet",
            "ProductImagesViewSet",
            "ProductImageDetail",
            "ProductReviewsViewSet",
            # Keyed by tenant + product id; every spell is written by
            # ``product.pricing.service`` through a Product save (a
            # ``post_save`` on ``product.Product``) or
            # ``sync_product_prices`` (``mark_surface_stale``).
            "product-price-history",
            "ProductTagsViewSet",
            "ProductAttributeViewSet",
            "ProductAttributeValueViewSet",
            "SearchProductViewSet",
            PRODUCT_SHIPPING_HANDLER,
        )
        + nuxt_functions("builderDataProduct", "builderDataSlug")
        # The rendered pages too, not just the JSON behind them:
        # /products and /products/** are SWR-cached (300s), so
        # without this a price edit stayed visible for the whole TTL.
        + nuxt_routes("/products"),
        related=("categories", "tags"),
        # The catalog feeds embed product rows (and the
        # category names used for g:product_type), and the
        # gateway caches them for FEED_FRESH_TTL in its own
        # Redis. Without this a price change took up to six
        # hours to reach Google, Meta and TikTok.
        invalidates_gateway_feeds=True,
        invalidated_by=(
            "product.Product",
            "product.ProductTranslation",
            "product.ProductImage",
            "product.ProductImageTranslation",
            "product.ProductHighlight",
            "product.ProductHighlightTranslation",
            "product.Attribute",
            "product.AttributeTranslation",
            "product.AttributeValue",
            "product.AttributeValueTranslation",
            "product.ProductAttribute",
            "product.Brand",
            "product.ProductVariantGroup",
            "product.ProductVariantGroupTranslation",
            # Moderation shows or hides a review on the product page;
            # a rate edit moves the average. A customer's new review
            # is pending, so it costs one batched purge, no more.
            "product.ProductReview",
            "product.ProductReviewTranslation",
        ),
        # Each order's stock decrement; the product pages show stock,
        # but checkout re-validates it and the page TTL bounds it. A
        # sale that sells the product out is not ignored
        # (``product.availability``): the listing puts sold-out
        # products last and the builder's lists read this surface's data.
        ignored_update_fields=SALE_UPDATE_FIELDS,
        icon="inventory_2",
        group="catalog",
    ),
    CacheSurface(
        code="categories",
        label=_("Categories"),
        description=_(
            "Category trees, listing pages and the category map"
            " used for the navbar."
        ),
        django_patterns=("*ProductCategoryViewSet_*",),
        nuxt_patterns=nuxt_handlers(
            "ProductCategoryViewSet",
            "ProductCategoryAll",
            "ProductCategoryDetail",
        )
        + nuxt_functions("builderDataCategory", "builderDataSlug")
        # Category pages live under /products/category/**, which the
        # same escaped-path prefix covers.
        + nuxt_routes("/products"),
        # The catalog feeds embed product rows (and the
        # category names used for g:product_type), and the
        # gateway caches them for FEED_FRESH_TTL in its own
        # Redis. Without this a price change took up to six
        # hours to reach Google, Meta and TikTok.
        invalidates_gateway_feeds=True,
        invalidated_by=(
            "product.ProductCategory",
            "product.ProductCategoryTranslation",
            "product.ProductCategoryImage",
            "product.ProductCategoryImageTranslation",
        ),
        icon="category",
        group="catalog",
    ),
)
