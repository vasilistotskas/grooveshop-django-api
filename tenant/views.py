from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response

from tenant.models import Tenant, TenantDomain
from tenant.serializers import TenantAdminSerializer, TenantConfigSerializer

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

    # Always query from public schema
    tenant_domain = (
        TenantDomain.objects.select_related("tenant")
        .filter(domain=domain, tenant__is_active=True)
        .first()
    )

    if tenant_domain is None:
        return Response(
            {"detail": "Store not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = TenantConfigSerializer(tenant_domain.tenant)
    cache.set(cache_key, serializer.data, TENANT_RESOLVE_CACHE_TTL)
    return Response(serializer.data)


class TenantAdminViewSet(viewsets.ModelViewSet):
    queryset = Tenant.objects.all()
    serializer_class = TenantAdminSerializer
    permission_classes = [IsAdminUser]

    def _require_public_schema(self):
        if connection.schema_name != "public":
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "This endpoint is only available on the public schema."
            )

    def get_queryset(self):
        if connection.schema_name != "public":
            return Tenant.objects.none()
        return super().get_queryset()

    def create(self, request, *args, **kwargs):
        self._require_public_schema()
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self._require_public_schema()
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._require_public_schema()
        return super().destroy(request, *args, **kwargs)
