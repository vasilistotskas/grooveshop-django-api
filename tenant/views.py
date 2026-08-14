from __future__ import annotations

import logging

from django.core.cache import cache
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from tenant.config import get_tenant_config, resolve_tenant_domains
from tenant.serializers import TenantConfigSerializer

logger = logging.getLogger(__name__)

TENANT_RESOLVE_CACHE_TTL = 3600  # 1 hour


@extend_schema(
    responses=TenantConfigSerializer,
    parameters=[
        OpenApiParameter(
            name="domain",
            type=str,
            location=OpenApiParameter.QUERY,
            required=True,
        ),
    ],
    tags=["Tenant"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def tenant_resolve(request: Request) -> Response:
    domain = request.query_params.get("domain", "")
    if not domain:
        return Response(
            {"detail": "domain query parameter is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    cache_key = f"tenant_resolve:{domain}"
    cached = cache.get(cache_key)
    if cached is not None:
        return Response(cached)

    if domain not in resolve_tenant_domains():
        return Response(
            {"detail": "Store not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = TenantConfigSerializer(get_tenant_config())
    cache.set(cache_key, serializer.data, TENANT_RESOLVE_CACHE_TTL)
    return Response(serializer.data)
