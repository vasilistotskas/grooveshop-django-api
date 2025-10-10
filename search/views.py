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
    summary=_("Search products"),
    description=_(
        "Search products using MeiliSearch. Provides full-text search with "
        "highlighting, ranking, and faceting capabilities. Results can be filtered by language."
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
def product_meili_search(request):
    query = request.query_params.get("query")
    if not query:
        raise ValidationError({"error": _("A search query is required.")})

    limit = int(request.query_params.get("limit", 10))
    offset = int(request.query_params.get("offset", 0))
    language_code = request.query_params.get("language_code")

    decoded_query = unquote(query)

    if language_code == "el":
        decoded_query = expand_greeklish_query(decoded_query, max_variants=5)

    search_qs = ProductTranslation.meilisearch.paginate(
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
        obj_data = ProductTranslationSerializer(obj, context=context).data
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
