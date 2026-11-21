from __future__ import annotations

from urllib.parse import unquote
from uuid import uuid4

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    permission_classes,
    throttle_classes,
)
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from blog.models.post import BlogPostTranslation
from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import (
    AnonIpRateThrottle,
    SearchThrottle,
    UserBudgetThrottle,
)
from search.analytics import record_engine_time
from search.params import (
    get_max_search_limit,
    parse_int,
    relax_query,
    validate_language_code,
)
from search.serializers import (
    BlogPostMeiliSearchResponseSerializer,
    BlogPostTranslationSerializer,
)
from tenant.permissions import IsBlogEnabled


# `@extend_schema` over `@api_view` is drf-spectacular's own documented
# pattern (see its FAQ). The suppressions on the decorators below are a ty
# limitation, not a defect here: `djangorestframework-stubs` declares
# `AsView` as `Protocol[_View]` with `__call__: _View` — an attribute
# annotated with the class's own TypeVar — and ty will not resolve that
# into callability when checking `TypeVar F, bound=Callable[..., Any]`.
# Reduced to 25 lines with no Django involved: the same Protocol with a
# concrete `__call__: Callable[..., Any]` passes, the generic form fails.
# Upstream master still declares it the same way, so a stubs bump does
# not help. Suppressed per line rather than per file so real
# argument-type errors in these modules are still reported.
@extend_schema(  # ty: ignore[invalid-argument-type]
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
                "Language code to filter results (e.g., 'en', 'el'). If not provided, searches all languages."
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
# Every blog viewset chains `IsBlogEnabled`; this endpoint serves the
# same content and did not, so a tenant whose plan has `blog_enabled`
# off still published its posts through search. `meili_filter` gates
# indexing on `is_published` only, never on the plan flag, so the
# documents are in the index either way — the gate has to be here.
@permission_classes([IsBlogEnabled])
@throttle_classes([SearchThrottle, AnonIpRateThrottle, UserBudgetThrottle])
def blog_post_meili_search(request):
    query = request.query_params.get("query")
    if not query:
        raise ValidationError(
            {"error": _("A search query is required.")}, code="query_required"
        )

    limit = min(
        parse_int(request.query_params.get("limit"), 10, "limit"),
        get_max_search_limit(),
    )
    offset = parse_int(request.query_params.get("offset"), 0, "offset")
    language_code = validate_language_code(
        request.query_params.get("language_code")
    )

    # Greeklish queries match the indexed ``*_greeklish`` shadow fields
    # directly (see src/search/transliteration.py) — no query rewriting.
    decoded_query = unquote(query)

    search_qs = BlogPostTranslation.meilisearch.paginate(
        limit=limit, offset=offset
    )

    # Always filter for published posts
    search_qs = search_qs.filter(is_published=True)

    if language_code:
        search_qs = search_qs.filter(language_code=language_code)
        search_qs = search_qs.locales(language_code)

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
            "query_id": uuid4(),
            "relaxed_query": relaxed_query,
            "limit": limit,
            "offset": offset,
            "estimated_total_hits": enriched_results["estimated_total_hits"],
            "results": serialized_data,
        },
        status=status.HTTP_200_OK,
    )
