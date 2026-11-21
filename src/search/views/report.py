from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from django.db.models import Avg, Count, Max
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core.api.permissions import IsStoreStaff
from core.api.serializers import ErrorResponseSerializer
from search.models import SearchClick, SearchQuery
from search.serializers import SearchAnalyticsResponseSerializer

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise


def _analytics_day(value: str | None, invalid: StrOrPromise) -> date | None:
    """Parse an analytics ``YYYY-MM-DD`` bound; ``None`` when absent."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError({"error": invalid}, code="invalid_date") from None


def _start_of_local_day(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


# The `ty: ignore` below is explained in `src/search/views/blog.py`.
@extend_schema(  # ty: ignore[invalid-argument-type]
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
# Per-store data, so the store's own staff may read it. It was
# gated on IsPlatformSuperuser, which on a tenant host reads
# is_superuser off a tenant-schema row — see is_platform_superuser.
@permission_classes([IsStoreStaff])
def search_analytics(request):
    """
    Aggregate and return search analytics metrics.

    Metrics:
    - Top 20 queries by frequency
    - Zero-result queries (queries that never returned results)
    - Search volume by content_type and language
    - Average results count
    - Average Meilisearch processing time. Engine time only — not
      end-to-end latency — summed across every engine call the request
      made (a relaxed-query retry is a second call). Recorded via
      ``record_engine_time`` on the request; rows predating that store
      NULL and are excluded from the average.
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

    # Apply date range filters. Both bounds are whole local days, so
    # the range is half-open: from the start day's first instant up to,
    # not including, the first instant of the day after end_date.
    start_date = _analytics_day(
        start_date_str,
        _("Invalid start_date format. Use ISO format: YYYY-MM-DD"),
    )
    end_date = _analytics_day(
        end_date_str,
        _("Invalid end_date format. Use ISO format: YYYY-MM-DD"),
    )
    if start_date and end_date and start_date > end_date:
        raise ValidationError(
            {"error": _("start_date must not be after end_date.")},
            code="date_range_invalid",
        )
    if start_date:
        queries_qs = queries_qs.filter(
            timestamp__gte=_start_of_local_day(start_date)
        )
    if end_date:
        queries_qs = queries_qs.filter(
            timestamp__lt=_start_of_local_day(end_date + timedelta(days=1))
        )

    # Apply content type filter
    if content_type_filter:
        if content_type_filter not in ["product", "blog_post", "federated"]:
            raise ValidationError(
                {
                    "error": _(
                        "Invalid content_type. Must be 'product', 'blog_post', or 'federated'"
                    )
                },
                code="invalid_content_type",
            )
        queries_qs = queries_qs.filter(content_type=content_type_filter)

    # Calculate total search count
    total_searches = queries_qs.count()

    # Per-query counts and clicks are computed in SEPARATE queries:
    # combining Count("id")/Avg("results_count") with Count("clicks") in
    # one annotate() fans out the JOIN to SearchClick before GROUP BY,
    # inflating counts and skewing averages for any query text whose
    # rows carry multiple clicks (Django's documented multi-aggregate
    # pitfall).
    top_query_rows = list(
        queries_qs.values("query")
        .annotate(
            count=Count("id"),
            avg_results=Avg("results_count"),
        )
        .order_by("-count")[:20]
    )
    clicks_by_query = dict(
        SearchClick.objects.filter(
            search_query__in=queries_qs,
            search_query__query__in=[row["query"] for row in top_query_rows],
        )
        .values_list("search_query__query")
        .annotate(clicks=Count("id"))
    )

    top_queries = []
    for query_data in top_query_rows:
        query_count = query_data["count"]
        avg_results = query_data["avg_results"]
        clicks_count = clicks_by_query.get(query_data["query"], 0)
        ctr = (clicks_count / query_count) if query_count > 0 else 0.0

        top_queries.append(
            {
                "query": query_data["query"],
                "count": query_count,
                "avg_results": round(avg_results, 2) if avg_results else 0.0,
                "click_through_rate": round(ctr, 4),
            }
        )

    # A query counts as zero-result only if it NEVER returned results
    # in the window (HAVING MAX(results_count) = 0): the storefront
    # fires product and blog sub-searches per user search, each
    # logged as its own row, so a per-row results_count=0 filter would
    # list queries whose other lane found plenty.
    zero_result_queries = (
        queries_qs.values("query", "language_code")
        .annotate(count=Count("id"), max_results=Max("results_count"))
        .filter(max_results=0)
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
        queries_qs.exclude(language_code__isnull=True)
        .exclude(language_code="")
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
