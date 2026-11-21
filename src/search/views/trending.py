from __future__ import annotations

from datetime import timedelta

from django.core.cache import caches
from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    permission_classes,
    throttle_classes,
)
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.api.throttling import (
    AnonIpRateThrottle,
    SearchThrottle,
    UserBudgetThrottle,
)
from search.models import SearchQuery
from search.params import VALID_LANGUAGE_CODES
from search.serializers import TrendingSearchResponseSerializer
from tenant.permissions import IsCatalogueEnabled

_TRENDING_CACHE_TTL_SECONDS = 300  # 5 minutes
_TRENDING_MIN_QUERY_LENGTH = 2
_TRENDING_DEFAULT_LIMIT = 8
_TRENDING_MAX_LIMIT = 20
_TRENDING_WINDOW_HOURS = 24


# The `ty: ignore` below is explained in `src/search/views/blog.py`.
@extend_schema(  # ty: ignore[invalid-argument-type]
    operation_id="listTrendingSearches",
    tags=["Search"],
    summary=_("List trending search queries"),
    description=_(
        "Return the most popular search queries from the last 24 hours, "
        "suitable for surfacing in the search modal empty state. Cached "
        "for 5 minutes per (language_code, content_type, limit)."
    ),
    responses={200: TrendingSearchResponseSerializer},
    parameters=[
        OpenApiParameter(
            name="language_code",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_("Filter queries by language (e.g. 'el')."),
            required=False,
        ),
        OpenApiParameter(
            name="content_type",
            type=str,
            location=OpenApiParameter.QUERY,
            description=_(
                "Filter by content type: product, blog_post, federated. "
                "Defaults to product."
            ),
            required=False,
        ),
        OpenApiParameter(
            name="limit",
            type=int,
            location=OpenApiParameter.QUERY,
            description=_("Max results. Default 8, cap 20."),
            required=False,
        ),
    ],
)
@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([SearchThrottle, AnonIpRateThrottle, UserBudgetThrottle])
def search_trending(request):
    """Return top search queries over the last 24h (Redis-cached)."""
    language_code = request.query_params.get("language_code") or ""
    content_type = request.query_params.get("content_type") or "product"
    if content_type == "product":
        # The queries are product searches: with no catalogue there is
        # nothing to suggest. Blog queries are the blog's own concern.
        IsCatalogueEnabled().has_permission(request, None)

    try:
        limit = int(request.query_params.get("limit", _TRENDING_DEFAULT_LIMIT))
    except TypeError, ValueError:
        limit = _TRENDING_DEFAULT_LIMIT
    limit = max(1, min(limit, _TRENDING_MAX_LIMIT))

    if (
        language_code
        and VALID_LANGUAGE_CODES
        and language_code not in VALID_LANGUAGE_CODES
    ):
        raise ValidationError(
            {"language_code": _("Unsupported language.")},
            code="unsupported_language",
        )

    cache_key = f"search:trending:{content_type}:{language_code}:{limit}"

    def _compute_trending():
        cutoff = timezone.now() - timedelta(hours=_TRENDING_WINDOW_HOURS)
        qs = (
            SearchQuery.objects.filter(
                timestamp__gte=cutoff,
                results_count__gt=0,
                content_type=content_type,
            )
            .exclude(query__isnull=True)
            .exclude(query__exact="")
        )
        if language_code:
            qs = qs.filter(language_code=language_code)

        top = (
            qs.values("query")
            .annotate(count=Count("id"))
            .order_by("-count")[: limit * 2]
        )

        results: list[dict] = []
        for row in top:
            query_text = (row.get("query") or "").strip()
            if len(query_text) < _TRENDING_MIN_QUERY_LENGTH:
                continue
            results.append({"query": query_text, "count": row["count"]})
            if len(results) >= limit:
                break

        return {
            "window_hours": _TRENDING_WINDOW_HOURS,
            "content_type": content_type,
            "language_code": language_code or None,
            "results": results,
        }

    # get_or_set uses an atomic add() internally, avoiding cache stampede
    # when multiple requests race on an expired key.
    payload = caches["default"].get_or_set(
        cache_key, _compute_trending, timeout=_TRENDING_CACHE_TTL_SECONDS
    )
    return Response(payload, status=status.HTTP_200_OK)
