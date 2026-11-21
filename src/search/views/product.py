from __future__ import annotations

from urllib.parse import unquote
from uuid import uuid4

from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.api.query_params import parse_int_csv
from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import AnonIpRateThrottle, UserBudgetThrottle
from product.attribute_selection import (
    ATTRIBUTE_VALUES_FIELD,
    AttributeSelection,
)
from product.models.category import ProductCategory
from product.models.product import ProductTranslation
from search.analytics import (
    COUNT_ONLY_MAX_LIMIT,
    mark_count_only,
    record_engine_time,
)
from search.params import (
    ALLOWED_PRODUCT_FACETS,
    PRODUCT_SORT_FIELD_MAP,
    get_max_search_limit,
    parse_bool_flag,
    parse_int,
    parse_optional_float,
    parse_optional_int,
    relax_query,
    validate_language_code,
)
from search.serializers import (
    ProductMeiliSearchResponseSerializer,
    ProductTranslationSerializer,
)
from search.throttles import SearchThrottle
from tenant.features import feature_on
from tenant.permissions import IsCatalogueEnabled

# The facets whose ``facetStats`` a filter sidebar reads as the range of the
# catalogue: with ``sidebarFacets`` they come from a query that ignores the
# shopper's filters. Price follows the scope category, the popularity ranges
# are store-wide.
_PRICE_STATS_FACETS: tuple[str, ...] = ("effective_price",)
_POPULARITY_STATS_FACETS: tuple[str, ...] = ("likes_count", "view_count")
_CATALOGUE_RANGE_FACETS = (*_PRICE_STATS_FACETS, *_POPULARITY_STATS_FACETS)


def _category_subtree_ids(category_ids: list[int]) -> list[int]:
    """Every category under the requested ones, the requested included.

    A category stands for its whole subtree: its page counts the products
    of every descendant (``recursive_product_count``), and a shopper who
    picks "Charging" means the cables and power banks filed under it too.
    The index stores each product's OWN category, so the filter has to
    name the descendants — filtering by the id alone listed 0 products on
    any category whose products all sit in its subcategories, under a
    header that said 77.

    Ids that match no category are kept, so a request for an unknown
    category still filters to nothing instead of dropping the filter.
    """
    requested = ProductCategory.objects.filter(id__in=category_ids)
    subtree = set(
        ProductCategory.objects.get_queryset_descendants(
            requested, include_self=True
        ).values_list("id", flat=True)
    )
    return sorted(subtree | set(category_ids))


def _visible_products(language_code: str | None):
    """The products a shopper can see at all: what every facet shares.

    Always the active, non-deleted ones, in the requested language when one
    is asked for.
    """
    visible = ProductTranslation.meilisearch.filter(
        active=True, is_deleted=False
    )
    if language_code:
        visible = visible.filter(language_code=language_code).locales(
            language_code
        )
    return visible


def _with_catalogue_ranges(
    search_qs, facets: list[str], language_code: str | None, scope_category
):
    """Take the requested price and popularity stats over the catalogue.

    The same ranges the storefront asked for in requests of their own:
    ``effective_price`` over the scope category's subtree (the whole
    catalogue without one), ``likes_count`` and ``view_count`` over the
    whole catalogue, none of them narrowed by the query or the filters.
    """
    if prices := [f for f in facets if f in _PRICE_STATS_FACETS]:
        priced = _visible_products(language_code)
        if scope_category is not None:
            priced = priced.filter(
                category__in=_category_subtree_ids([scope_category])
            )
        search_qs = search_qs.stats_over(priced, *prices)
    if popularities := [f for f in facets if f in _POPULARITY_STATS_FACETS]:
        search_qs = search_qs.stats_over(
            _visible_products(language_code), *popularities
        )
    return search_qs


# The `ty: ignore` below is explained in `src/search/views/blog.py`.
@extend_schema(  # ty: ignore[invalid-argument-type]
    summary=_("Search products with advanced filters"),
    description=_(
        "Search products using MeiliSearch with support for full-text search, "
        "price range, popularity, view count, and category filters. "
        "Returns facet distribution and statistics for dynamic filter UI."
    ),
    tags=["Search"],
    responses={
        200: ProductMeiliSearchResponseSerializer,
        400: ErrorResponseSerializer,
    },
    parameters=[
        OpenApiParameter(
            name="query",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "Full-text search query (empty for no search filter)"
            ),
            required=False,
        ),
        OpenApiParameter(
            name="language_code",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "Language code to filter results (e.g., 'en', 'el'). If not provided, searches all languages."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="price_min",
            type=float,
            location=OpenApiParameter.QUERY,
            description=_(
                "Minimum price filter (effective_price >= value): the price "
                "the customer pays, unconditional automatic promotions "
                "included"
            ),
            required=False,
        ),
        OpenApiParameter(
            name="price_max",
            type=float,
            location=OpenApiParameter.QUERY,
            description=_(
                "Maximum price filter (effective_price <= value): the price "
                "the customer pays, unconditional automatic promotions "
                "included"
            ),
            required=False,
        ),
        OpenApiParameter(
            name="likes_min",
            type=int,
            location=OpenApiParameter.QUERY,
            description=_("Minimum likes filter (likes_count >= value)"),
            required=False,
        ),
        OpenApiParameter(
            name="views_min",
            type=int,
            location=OpenApiParameter.QUERY,
            description=_("Minimum views filter (view_count >= value)"),
            required=False,
        ),
        OpenApiParameter(
            name="categories",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "Comma-separated category IDs; each matches its whole "
                "subtree (the category and every descendant)"
            ),
            required=False,
        ),
        OpenApiParameter(
            name="attributeValues",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "Comma-separated attribute value IDs. Values of the same "
                "attribute are alternatives (any of them); values of "
                "different attributes must all match. With the "
                "attribute_values facet, an attribute's own values are "
                "counted without its own selection."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="brands",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_("Comma-separated brand IDs (brand IN [ids])"),
            required=False,
        ),
        OpenApiParameter(
            name="inStock",
            type=OpenApiTypes.BOOL,
            location=OpenApiParameter.QUERY,
            description=_("When true, only products with stock above zero"),
            required=False,
        ),
        OpenApiParameter(
            name="onOffer",
            type=OpenApiTypes.BOOL,
            location=OpenApiParameter.QUERY,
            description=_(
                "When true, only products on offer: carrying a markdown "
                "(discount_percent > 0) or covered by a live automatic "
                "promotion on specific products or categories (free "
                "shipping, order-wide and coupon-code promotions do not "
                "count)."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="sort",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "Sort field (effectivePrice, -effectivePrice, -likesCount, "
                "-viewCount, -createdAt)."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="facets",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "Comma-separated facet fields for counts and stats. A "
                "request for facets with limit 0 or 1 is a facet read and "
                "is not recorded as a search."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="sidebarFacets",
            type=OpenApiTypes.BOOL,
            location=OpenApiParameter.QUERY,
            description=_(
                "When true, the facets answer a filter sidebar in this one "
                "request. The category and brand counts leave out their own "
                "selection (the attribute_values counts always do), so "
                "picking one does not hide its alternatives. The "
                "effective_price stats are those of the whole catalogue, or "
                "of scopeCategory's subtree, and the likes_count and "
                "view_count stats those of the whole catalogue: none follows "
                "the query or the other filters, so a slider's range does "
                "not move as filters change."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="scopeCategory",
            type=int,
            location=OpenApiParameter.QUERY,
            description=_(
                "The category a listing page is about, which scopes the "
                "effective_price stats of sidebarFacets to its subtree. "
                "Unlike categories, it is not a filter of the results."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="limit",
            type=int,
            location=OpenApiParameter.QUERY,
            description=_("Maximum number of results to return"),
            required=False,
            default=20,
        ),
        OpenApiParameter(
            name="offset",
            type=int,
            location=OpenApiParameter.QUERY,
            description=_("Number of results to skip"),
            required=False,
            default=0,
        ),
    ],
)
@api_view(["GET"])
# Explicit, per the project rule that an anonymous endpoint declares
# itself rather than leaning on DEFAULT_PERMISSION_CLASSES.
@permission_classes([IsCatalogueEnabled, AllowAny])
@throttle_classes([SearchThrottle, AnonIpRateThrottle, UserBudgetThrottle])
def product_meili_search(request):
    """Search products with advanced filtering via Meilisearch."""

    # Parse query parameters
    query = request.query_params.get("query", "")
    limit = min(
        parse_int(request.query_params.get("limit"), 20, "limit"),
        get_max_search_limit(),
    )
    offset = parse_int(request.query_params.get("offset"), 0, "offset")
    language_code = validate_language_code(
        request.query_params.get("language_code")
    )

    # Parse filter parameters. These come from external callers, so parse
    # them defensively — invalid numbers must return 400, not raise an
    # unhandled ValueError (a 500 on the public search endpoint). Note the
    # keys are snake_case here: the CamelCaseMiddleWare has already
    # underscoreized the incoming camelCase query-string keys (e.g. the
    # client sends ``attributeValues``, which arrives as ``attribute_values``).
    price_min = parse_optional_float(
        request.query_params.get("price_min"), "price_min"
    )
    price_max = parse_optional_float(
        request.query_params.get("price_max"), "price_max"
    )
    likes_min = parse_optional_int(
        request.query_params.get("likes_min"), "likes_min"
    )
    views_min = parse_optional_int(
        request.query_params.get("views_min"), "views_min"
    )
    category_ids = parse_int_csv(
        request.query_params.get("categories"), "categories"
    )
    attribute_value_ids = parse_int_csv(
        request.query_params.get("attribute_values"), "attribute_values"
    )
    brand_ids = parse_int_csv(request.query_params.get("brands"), "brands")
    in_stock = parse_bool_flag(request.query_params.get("in_stock"), "inStock")
    on_offer = parse_bool_flag(request.query_params.get("on_offer"), "onOffer")
    sidebar_facets = parse_bool_flag(
        request.query_params.get("sidebar_facets"), "sidebarFacets"
    )
    scope_category = parse_optional_int(
        request.query_params.get("scope_category"), "scopeCategory"
    )
    sort_param = request.query_params.get("sort")

    # Parse facets parameter — restrict to known-safe facet fields.
    facets_param = request.query_params.get("facets", "")
    facets = [
        f
        for f in (fs.strip() for fs in facets_param.split(","))
        if f and f in ALLOWED_PRODUCT_FACETS
    ]
    if facets and limit <= COUNT_ONLY_MAX_LIMIT:
        mark_count_only(request)

    # Greeklish queries match the indexed ``*_greeklish`` shadow fields
    # directly (see src/search/transliteration.py) — no query rewriting.
    decoded_query = unquote(query)

    search_qs = _visible_products(language_code).paginate(
        limit=limit, offset=offset
    )

    # Apply price range filters
    if price_min is not None:
        search_qs = search_qs.filter(effective_price__gte=price_min)
    if price_max is not None:
        search_qs = search_qs.filter(effective_price__lte=price_max)

    # Apply popularity filters
    if likes_min is not None:
        search_qs = search_qs.filter(likes_count__gte=likes_min)
    if views_min is not None:
        search_qs = search_qs.filter(view_count__gte=views_min)

    # Apply category filter (multi-select with IN operator), each
    # requested category standing for its whole subtree. With sidebarFacets
    # the category counts leave this clause out.
    if category_ids:
        category_filter = _category_subtree_ids(category_ids)
        if sidebar_facets and "category" in facets:
            search_qs = search_qs.filter_facet_group(
                "category", category_filter, members=None
            )
        else:
            search_qs = search_qs.filter(category__in=category_filter)

    # Apply the attribute value selection: alternatives within one
    # attribute, every attribute narrowing the result (AttributeSelection).
    # With the attribute_values facet requested, each attribute's own
    # values are counted without its own selection.
    if attribute_value_ids:
        search_qs = AttributeSelection.from_value_ids(
            attribute_value_ids
        ).filter_search(search_qs, with_counts=ATTRIBUTE_VALUES_FIELD in facets)

    if brand_ids:
        if sidebar_facets and "brand" in facets:
            search_qs = search_qs.filter_facet_group(
                "brand", brand_ids, members=None
            )
        else:
            search_qs = search_qs.filter(brand__in=brand_ids)
    if in_stock:
        search_qs = search_qs.filter(in_stock=True)
    if on_offer:
        search_qs = search_qs.filter(on_offer=True)

    # Apply sort — map the camelCase value to the snake_case field the index
    # exposes; unknown fields are dropped to prevent DSL injection.
    if sort_param and sort_param in PRODUCT_SORT_FIELD_MAP:
        search_qs = search_qs.order_by(PRODUCT_SORT_FIELD_MAP[sort_param])

    if sidebar_facets:
        search_qs = _with_catalogue_ranges(
            search_qs, facets, language_code, scope_category
        )
        facets = [f for f in facets if f not in _CATALOGUE_RANGE_FACETS]

    # Add facets for dynamic filter counts and stats
    if facets:
        search_qs = search_qs.set_facets(*facets)

    enriched_results = search_qs.search(q=decoded_query)
    record_engine_time(request, enriched_results.get("processing_time_ms"))

    relaxed_query = None
    if not enriched_results["results"] and (
        relaxed := relax_query(decoded_query)
    ):
        enriched_results = search_qs.search(q=relaxed)
        record_engine_time(request, enriched_results.get("processing_time_ms"))
        if enriched_results["results"]:
            relaxed_query = relaxed

    reviews_visible = feature_on("product_reviews")
    serialized_data = []
    for result in enriched_results["results"]:
        obj = result["object"]
        context = {
            "reviews_visible": reviews_visible,
            "_formatted": result.get("_formatted", {}),
            "_matchesPosition": result.get("_matchesPosition", {}),
            "_rankingScore": result.get("_rankingScore", None),
        }
        obj_data = ProductTranslationSerializer(obj, context=context).data
        serialized_data.append(obj_data)

    response_data = {
        "query_id": uuid4(),
        "relaxed_query": relaxed_query,
        "limit": limit,
        "offset": offset,
        "estimated_total_hits": enriched_results["estimated_total_hits"],
        "results": serialized_data,
    }

    # Add facet data if requested
    if "facetDistribution" in enriched_results:
        response_data["facet_distribution"] = enriched_results[
            "facetDistribution"
        ]
    if "facetStats" in enriched_results:
        response_data["facet_stats"] = enriched_results["facetStats"]

    return Response(response_data, status=status.HTTP_200_OK)
