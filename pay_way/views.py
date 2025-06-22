from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema_view
from rest_framework.filters import SearchFilter

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods
from pay_way.filters import PayWayFilter
from pay_way.models import PayWay
from pay_way.serializers import (
    PayWayDetailSerializer,
    PayWaySerializer,
    PayWayWriteSerializer,
)


@extend_schema_view(
    **create_schema_view_config(
        model_class=PayWay,
        display_config={
            "tag": "Payment methods",
        },
        serializers={
            "list_serializer": PayWaySerializer,
            "detail_serializer": PayWayDetailSerializer,
            "write_serializer": PayWayWriteSerializer,
        },
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class PayWayViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = PayWay.objects.all()
    serializers = {
        "default": PayWayDetailSerializer,
        "list": PayWaySerializer,
        "retrieve": PayWayDetailSerializer,
        "create": PayWayWriteSerializer,
        "update": PayWayWriteSerializer,
        "partial_update": PayWayWriteSerializer,
    }
    response_serializers = {
        "create": PayWayDetailSerializer,
        "update": PayWayDetailSerializer,
        "partial_update": PayWayDetailSerializer,
    }
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = PayWayFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "cost",
        "free_threshold",
        "provider_code",
        "is_online_payment",
        "requires_confirmation",
        "sort_order",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "provider_code",
        "translations__name",
        "translations__description",
        "translations__instructions",
    ]
