from __future__ import annotations

from urllib.parse import unquote

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from blog.models.post import BlogPostTranslation
from core.api.serializers import ErrorResponseSerializer
from core.utils.greeklish import expand_greeklish_query
from product.models.product import ProductTranslation
from search.serializers import (
    BlogPostMeiliSearchResponseSerializer,
    BlogPostTranslationSerializer,
    ProductMeiliSearchResponseSerializer,
    ProductTranslationSerializer,
)


@extend_schema(
    summary=_("Search blog posts"),
    description=_(
        "Search blog posts using MeiliSearch. Provides full-text search with "
        "highlighting, ranking, and faceting capabilities. Results can be filtered by language."
    ),
    tags=["Search"],
    responses={
        200: BlogPostMeiliSearchResponseSerializer,
        400: ErrorResponseSerializer,
    },
    parameters=[
        OpenApiParameter(
            name="query",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_("Search query string"),
            required=True,
        ),
        OpenApiParameter(
            name="language_code",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "Language code to filter results (e.g., 'en', 'el', 'de'). If not provided, searches all languages."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="limit",
            type=int,
            location=OpenApiParameter.QUERY,
            description=_("Maximum number of results to return"),
            required=False,
            default=10,
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
def blog_post_meili_search(request):
    query = request.query_params.get("query")
    if not query:
        raise ValidationError({"error": _("A search query is required.")})

    limit = int(request.query_params.get("limit", 10))
    offset = int(request.query_params.get("offset", 0))
    language_code = request.query_params.get("language_code")

    decoded_query = unquote(query)

    if language_code == "el":
        decoded_query = expand_greeklish_query(decoded_query, max_variants=5)

    search_qs = BlogPostTranslation.meilisearch.paginate(
        limit=limit, offset=offset
    )

    if language_code:
        search_qs = search_qs.filter(language_code=language_code)
        search_qs = search_qs.locales(language_code)

    enriched_results = search_qs.search(q=decoded_query)

    serialized_data = []
    for result in enriched_results["results"]:
        obj = result["object"]
        context = {
            "_formatted": result.get("_formatted", {}),
            "_matchesPosition": result.get("_matchesPosition", {}),
            "_rankingScore": result.get("_rankingScore", None),
        }
        obj_data = BlogPostTranslationSerializer(obj, context=context).data
        serialized_data.append(obj_data)

    return Response(
        {
            "limit": limit,
            "offset": offset,
            "estimated_total_hits": enriched_results["estimated_total_hits"],
            "results": serialized_data,
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(
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
                "Language code to filter results (e.g., 'en', 'el', 'de'). If not provided, searches all languages."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="price_min",
            type=float,
            location=OpenApiParameter.QUERY,
            description=_("Minimum price filter (final_price >= value)"),
            required=False,
        ),
        OpenApiParameter(
            name="price_max",
            type=float,
            location=OpenApiParameter.QUERY,
            description=_("Maximum price filter (final_price <= value)"),
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
            description=_("Comma-separated category IDs (category IN [ids])"),
            required=False,
        ),
        OpenApiParameter(
            name="sort",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "Sort field (finalPrice, -finalPrice, -likesCount, -viewCount, -createdAt)"
            ),
            required=False,
        ),
        OpenApiParameter(
            name="facets",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_("Comma-separated facet fields for counts and stats"),
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
def product_meili_search(request):
    """Search products with advanced filtering via Meilisearch."""

    # Parse query parameters
    query = request.query_params.get("query", "")
    limit = int(request.query_params.get("limit", 20))
    offset = int(request.query_params.get("offset", 0))
    language_code = request.query_params.get("language_code")

    # Parse filter parameters
    price_min = request.query_params.get("price_min")
    price_max = request.query_params.get("price_max")
    likes_min = request.query_params.get("likes_min")
    views_min = request.query_params.get("views_min")
    categories_param = request.query_params.get("categories", "")
    sort_param = request.query_params.get("sort")

    # Parse facets parameter
    facets_param = request.query_params.get("facets", "")
    facets = [f.strip() for f in facets_param.split(",") if f.strip()]

    # Parse categories (comma-separated)
    categories = [c.strip() for c in categories_param.split(",") if c.strip()]

    # Decode and expand query for Greek language
    decoded_query = unquote(query)
    if language_code == "el":
        decoded_query = expand_greeklish_query(decoded_query, max_variants=5)

    search_qs = ProductTranslation.meilisearch.paginate(
        limit=limit, offset=offset
    )

    if language_code:
        search_qs = search_qs.filter(language_code=language_code)
        search_qs = search_qs.locales(language_code)

    # Apply price range filters
    if price_min:
        search_qs = search_qs.filter(final_price__gte=float(price_min))
    if price_max:
        search_qs = search_qs.filter(final_price__lte=float(price_max))

    # Apply popularity filters
    if likes_min:
        search_qs = search_qs.filter(likes_count__gte=int(likes_min))
    if views_min:
        search_qs = search_qs.filter(view_count__gte=int(views_min))

    # Apply category filter (multi-select with IN operator)
    if categories:
        category_ids = [int(c) for c in categories]
        search_qs = search_qs.filter(category__in=category_ids)

    # Apply sort
    if sort_param:
        search_qs = search_qs.order_by(sort_param)

    # Add facets for dynamic filter counts and stats
    if facets:
        search_qs = search_qs.facets(*facets)

    enriched_results = search_qs.search(q=decoded_query)

    serialized_data = []
    for result in enriched_results["results"]:
        obj = result["object"]
        context = {
            "_formatted": result.get("_formatted", {}),
            "_matchesPosition": result.get("_matchesPosition", {}),
            "_rankingScore": result.get("_rankingScore", None),
        }
        obj_data = ProductTranslationSerializer(obj, context=context).data
        serialized_data.append(obj_data)

    response_data = {
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
