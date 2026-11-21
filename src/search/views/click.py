from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import AnonIpRateThrottle, UserBudgetThrottle
from search.serializers import (
    SearchClickRequestSerializer,
    SearchClickResponseSerializer,
)
from search.throttles import SearchClickThrottle
from tenant.celery import publish_or_drop


# The `ty: ignore` below is explained in `src/search/views/blog.py`.
@extend_schema(  # ty: ignore[invalid-argument-type]
    summary=_("Log a click on a search result"),
    description=_(
        "Attribute a click on a search result to the query that produced "
        "it. The query_id comes from the search response. Feeds the "
        "click-through ranking signal and search analytics."
    ),
    tags=["Search"],
    request=SearchClickRequestSerializer,
    responses={
        202: SearchClickResponseSerializer,
        400: ErrorResponseSerializer,
    },
)
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([SearchClickThrottle, AnonIpRateThrottle, UserBudgetThrottle])
def search_click(request):
    serializer = SearchClickRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    from search.tasks import save_search_click

    publish_or_drop(
        save_search_click,
        query_uuid=str(data["query_id"]),
        result_id=str(data["result_id"]),
        result_type=data["result_type"],
        position=data["position"],
    )
    return Response({"detail": _("Accepted.")}, status=status.HTTP_202_ACCEPTED)
