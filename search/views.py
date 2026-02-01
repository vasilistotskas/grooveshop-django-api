from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import unquote

from django.db.models import Avg, Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from blog.models.post import BlogPostTranslation
from core.api.serializers import ErrorResponseSerializer
from core.utils.greeklish import expand_greeklish_query
from meili._client import client as meili_client
from product.models.product import ProductTranslation
from search.models import SearchClick, SearchQuery
from search.serializers import (
    BlogPostMeiliSearchResponseSerializer,
    BlogPostTranslationSerializer,
    FederatedSearchResponseSerializer,
    ProductMeiliSearchResponseSerializer,
    ProductTranslationSerializer,
    SearchAnalyticsResponseSerializer,
)

logger = logging.getLogger(__name__)


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
        search_qs = search_qs.set_facets(*facets)

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


@extend_schema(
    summary=_("Federated search across products and blog posts"),
    description=_(
        "Search multiple content types simultaneously using Meilisearch "
        "multi_search API with federation mode. Results are weighted and merged "
        "with products having weight 1.0 and blog posts having weight 0.7."
    ),
    tags=["Search"],
    responses={
        200: FederatedSearchResponseSerializer,
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
                "Language code to filter results (e.g., 'en', 'el', 'de'). "
                "If not provided, searches all languages."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="limit",
            type=int,
            location=OpenApiParameter.QUERY,
            description=_("Maximum total number of results to return"),
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
def federated_search(request):
    """
    Execute federated search using Meilisearch multi_search API.

    Algorithm:
    1. Parse and validate query parameters
    2. Apply Greeklish expansion if language_code is 'el'
    3. Build multi_search queries for ProductTranslation and BlogPostTranslation
    4. Set federation weights (products: 1.0, blog_posts: 0.7)
    5. Calculate result allocation (70% products, 30% blog posts)
    6. Execute multi_search with federation mode
    7. Enrich results with Django ORM objects
    8. Return unified results with content_type field
    """
    # Parse and validate query parameters
    query = request.query_params.get("query")
    if not query:
        raise ValidationError({"error": _("A search query is required.")})

    limit = int(request.query_params.get("limit", 20))
    offset = int(request.query_params.get("offset", 0))
    language_code = request.query_params.get("language_code")

    # Decode and expand query for Greek language
    decoded_query = unquote(query)
    if language_code == "el":
        decoded_query = expand_greeklish_query(decoded_query, max_variants=5)

    # Calculate result allocation (70% products, 30% blog posts)
    product_limit = int(limit * 0.7)
    limit - product_limit

    # Build filters for language and content filtering
    product_filters = []
    blog_filters = []

    if language_code:
        product_filters.append(f"language_code = '{language_code}'")
        blog_filters.append(f"language_code = '{language_code}'")

    # Content filtering: exclude inactive/deleted products
    product_filters.append("active = true")
    product_filters.append("is_deleted = false")

    # Content filtering: exclude unpublished blog posts
    blog_filters.append("is_published = true")

    # Build multi_search queries with federation
    try:
        multi_search_params = {
            "federation": {
                "limit": limit,
                "offset": offset,
            },
            "queries": [
                {
                    "indexUid": ProductTranslation._meilisearch["index_name"],
                    "q": decoded_query,
                    "filter": product_filters,
                    "showMatchesPosition": True,
                    "showRankingScore": True,
                    "attributesToRetrieve": ["*"],
                    "federationOptions": {"weight": 1.0},
                },
                {
                    "indexUid": BlogPostTranslation._meilisearch["index_name"],
                    "q": decoded_query,
                    "filter": blog_filters,
                    "showMatchesPosition": True,
                    "showRankingScore": True,
                    "attributesToRetrieve": ["*"],
                    "federationOptions": {"weight": 0.7},
                },
            ],
        }

        # Execute multi_search with federation
        results = meili_client.client.multi_search(
            queries=multi_search_params["queries"],
            federation=multi_search_params["federation"],
        )

    except Exception as e:
        logger.error(f"Federated search failed: {str(e)}")
        raise ValidationError(
            {"error": _("Search failed. Please try again later.")}
        )

    # Extract hits and metadata
    hits = results.get("hits", [])
    estimated_total_hits = results.get("estimatedTotalHits", 0)

    # Enrich results with Django ORM objects and add content_type
    enriched_results = []

    for hit in hits:
        try:
            # Get federation metadata
            federation_metadata = hit.get("_federation", {})
            index_uid = federation_metadata.get("indexUid", "")

            # Determine content type from index
            if "ProductTranslation" in index_uid:
                model = ProductTranslation
                serializer_class = ProductTranslationSerializer
            elif "BlogPostTranslation" in index_uid:
                model = BlogPostTranslation
                serializer_class = BlogPostTranslationSerializer
            else:
                logger.warning(f"Unknown index UID: {index_uid}")
                continue

            # Fetch Django object
            obj_id = hit.get("id")
            if not obj_id:
                continue

            try:
                obj = model.objects.get(pk=obj_id)
            except model.DoesNotExist:
                logger.warning(
                    f"{model.__name__} with id {obj_id} not found in database"
                )
                continue

            # Prepare context with Meilisearch metadata
            context = {
                "_formatted": hit.get("_formatted", {}),
                "_matchesPosition": hit.get("_matchesPosition", {}),
                "_rankingScore": hit.get("_rankingScore", None),
            }

            # Serialize object
            obj_data = serializer_class(obj, context=context).data

            # Add federation metadata
            obj_data["_federation"] = federation_metadata

            enriched_results.append(obj_data)

        except Exception as e:
            logger.error(f"Error enriching result: {str(e)}")
            continue

    # Return response
    return Response(
        {
            "limit": limit,
            "offset": offset,
            "estimated_total_hits": estimated_total_hits,
            "results": enriched_results,
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(
    summary=_("Get search analytics metrics"),
    description=_(
        "Retrieve aggregated search analytics including top queries, "
        "zero-result queries, search volume by content type and language, "
        "average results count, average processing time, and click-through rate. "
        "Results can be filtered by date range and content type."
    ),
    tags=["Search"],
    responses={
        200: SearchAnalyticsResponseSerializer,
        400: ErrorResponseSerializer,
    },
    parameters=[
        OpenApiParameter(
            name="start_date",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "Start date for analytics range (ISO format: YYYY-MM-DD). "
                "If not provided, includes all historical data."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="end_date",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "End date for analytics range (ISO format: YYYY-MM-DD). "
                "If not provided, includes data up to current date."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="content_type",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "Filter by content type: 'product', 'blog_post', or 'federated'. "
                "If not provided, includes all content types."
            ),
            required=False,
        ),
    ],
)
@api_view(["GET"])
def search_analytics(request):
    """
    Aggregate and return search analytics metrics.

    Metrics:
    - Top 20 queries by frequency
    - Zero-result queries (results_count = 0)
    - Search volume by content_type and language
    - Average results count
    - Average processing time
    - Click-through rate (clicks / searches)

    Filters:
    - Date range (start_date, end_date)
    - Content type (product, blog_post, federated)
    """
    # Parse date range parameters
    start_date_str = request.query_params.get("start_date")
    end_date_str = request.query_params.get("end_date")
    content_type_filter = request.query_params.get("content_type")

    # Build base queryset
    queries_qs = SearchQuery.objects.all()

    # Apply date range filters
    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str)
            queries_qs = queries_qs.filter(timestamp__gte=start_date)
        except ValueError:
            raise ValidationError(
                {
                    "error": _(
                        "Invalid start_date format. Use ISO format: YYYY-MM-DD"
                    )
                }
            )

    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str)
            queries_qs = queries_qs.filter(timestamp__lte=end_date)
        except ValueError:
            raise ValidationError(
                {
                    "error": _(
                        "Invalid end_date format. Use ISO format: YYYY-MM-DD"
                    )
                }
            )

    # Apply content type filter
    if content_type_filter:
        if content_type_filter not in ["product", "blog_post", "federated"]:
            raise ValidationError(
                {
                    "error": _(
                        "Invalid content_type. Must be 'product', 'blog_post', or 'federated'"
                    )
                }
            )
        queries_qs = queries_qs.filter(content_type=content_type_filter)

    # Calculate total search count
    total_searches = queries_qs.count()

    # Aggregate top queries (top 20 by frequency)
    top_queries_data = (
        queries_qs.values("query")
        .annotate(count=Count("id"), avg_results=Avg("results_count"))
        .order_by("-count")[:20]
    )

    # Calculate click-through rate for each top query
    top_queries = []
    for query_data in top_queries_data:
        query_text = query_data["query"]
        query_count = query_data["count"]
        avg_results = query_data["avg_results"]

        # Count clicks for this query
        clicks_count = SearchClick.objects.filter(
            search_query__query=query_text
        ).count()

        # Calculate CTR (clicks / searches)
        ctr = (clicks_count / query_count) if query_count > 0 else 0.0

        top_queries.append(
            {
                "query": query_text,
                "count": query_count,
                "avg_results": round(avg_results, 2) if avg_results else 0.0,
                "click_through_rate": round(ctr, 4),
            }
        )

    # Aggregate zero-result queries (results_count = 0)
    zero_result_queries = (
        queries_qs.filter(results_count=0)
        .values("query", "language_code")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )

    zero_result_queries_list = [
        {
            "query": item["query"],
            "count": item["count"],
            "language_code": item["language_code"] or "unknown",
        }
        for item in zero_result_queries
    ]

    # Calculate search volume by content_type
    volume_by_content_type = dict(
        queries_qs.values("content_type")
        .annotate(count=Count("id"))
        .values_list("content_type", "count")
    )

    # Calculate search volume by language
    volume_by_language = dict(
        queries_qs.filter(language_code__isnull=False)
        .values("language_code")
        .annotate(count=Count("id"))
        .values_list("language_code", "count")
    )

    # Calculate average results count
    avg_results_count = (
        queries_qs.aggregate(avg=Avg("results_count"))["avg"] or 0.0
    )

    # Calculate average processing time (only for queries with processing_time_ms)
    avg_processing_time = (
        queries_qs.filter(processing_time_ms__isnull=False).aggregate(
            avg=Avg("processing_time_ms")
        )["avg"]
        or 0.0
    )

    # Calculate overall click-through rate
    total_clicks = SearchClick.objects.filter(
        search_query__in=queries_qs
    ).count()
    overall_ctr = (total_clicks / total_searches) if total_searches > 0 else 0.0

    # Build response
    response_data = {
        "date_range": {
            "start": start_date_str or "all",
            "end": end_date_str or "now",
        },
        "top_queries": top_queries,
        "zero_result_queries": zero_result_queries_list,
        "search_volume": {
            "total": total_searches,
            "by_content_type": volume_by_content_type,
            "by_language": volume_by_language,
        },
        "performance": {
            "avg_processing_time_ms": round(avg_processing_time, 2),
            "avg_results_count": round(avg_results_count, 2),
        },
        "click_through_rate": round(overall_ctr, 4),
    }

    return Response(response_data, status=status.HTTP_200_OK)
