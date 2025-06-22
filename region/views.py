from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods
from region.filters import RegionFilter
from region.models import Region
from region.serializers import (
    RegionDetailSerializer,
    RegionSerializer,
    RegionWriteSerializer,
)


@extend_schema_view(
    **create_schema_view_config(
        model_class=Region,
        display_config={
            "tag": "Regions",
        },
        serializers={
            "list_serializer": RegionSerializer,
            "detail_serializer": RegionDetailSerializer,
            "write_serializer": RegionWriteSerializer,
        },
        error_serializer=ErrorResponseSerializer,
    ),
    get_regions_by_country_alpha_2=extend_schema(
        operation_id="listRegionsByCountry",
        summary=_("Get regions by country"),
        description=_(
            "Get all regions for a specific country using its alpha-2 code."
        ),
        tags=["Geography"],
        responses={
            200: RegionSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(
    settings.DEFAULT_CACHE_TTL,
    methods=["list", "retrieve", "get_regions_by_country_alpha_2"],
)
class RegionViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = Region.objects.all()
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = RegionFilter
    ordering_fields = ["created_at", "alpha", "sort_order"]
    ordering = ["-created_at"]
    search_fields = ["alpha", "translations__name", "country__alpha_2"]

    serializers = {
        "list": RegionSerializer,
        "create": RegionWriteSerializer,
        "update": RegionWriteSerializer,
        "partial_update": RegionWriteSerializer,
        "retrieve": RegionDetailSerializer,
        "get_regions_by_country_alpha_2": RegionSerializer,
    }

    @action(
        detail=True,
        methods=["GET"],
    )
    def get_regions_by_country_alpha_2(self, request, pk=None, *args, **kwargs):
        regions = Region.objects.filter(country__alpha_2=pk)
        serializer = self.get_serializer(regions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
