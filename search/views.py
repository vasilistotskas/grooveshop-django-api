from __future__ import annotations

from urllib.parse import unquote

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from blog.models.post import BlogPostTranslation
from product.models.product import ProductTranslation
from search.serializers import BlogPostTranslationSerializer
from search.serializers import ProductTranslationSerializer


@api_view(["GET"])
def blog_post_meili_search(request):
    query = request.query_params.get("query")
    if not query:
        raise ValidationError({"error": "A search query is required."})

    limit = int(request.query_params.get("limit", 10))
    offset = int(request.query_params.get("offset", 0))

    decoded_query = unquote(query)

    enriched_results = BlogPostTranslation.meilisearch.paginate(limit=limit, offset=offset).search(
        q=decoded_query
    )

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


@api_view(["GET"])
def product_meili_search(request):
    query = request.query_params.get("query")
    if not query:
        raise ValidationError({"error": "A search query is required."})

    limit = int(request.query_params.get("limit", 10))
    offset = int(request.query_params.get("offset", 0))

    decoded_query = unquote(query)

    enriched_results = ProductTranslation.meilisearch.paginate(limit=limit, offset=offset).search(
        q=decoded_query
    )

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
