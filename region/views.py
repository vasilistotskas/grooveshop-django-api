from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
from rest_framework import status
from rest_framework.decorators import action

from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from core.utils.views import cache_methods
from region.filters import RegionFilter
from region.models import Region
from region.serializers import (
    RegionDetailSerializer,
    RegionSerializer,
    RegionWriteSerializer,
)

serializers_config: SerializersConfig = {
    **crud_config(
        list=RegionSerializer,
        detail=RegionDetailSerializer,
        write=RegionWriteSerializer,
    ),
    "get_regions_by_country_alpha_2": ActionConfig(
        response=RegionSerializer,
        many=True,
        operation_id="listRegionsByCountry",
        summary=_("Get regions by country"),
        description=_(
            "Get all regions for a specific country using its alpha-2 code."
        ),
        tags=["Geography"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=Region,
        display_config={
            "tag": "Regions",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    ),
)
@cache_methods(
    settings.DEFAULT_CACHE_TTL,
    methods=["list", "retrieve", "get_regions_by_country_alpha_2"],
)
class RegionViewSet(BaseModelViewSet):
    queryset = Region.objects.all()
    filterset_class = RegionFilter
    ordering_fields = ["created_at", "alpha", "sort_order"]
    ordering = ["-created_at"]
    search_fields = ["alpha", "translations__name", "country__alpha_2"]
    serializers_config = serializers_config

    def get_queryset(self):
        """
        Return optimized queryset based on action.

        Uses Region.objects.for_list() for list views and
        Region.objects.for_detail() for detail views.
        """
        if self.action == "list":
            return Region.objects.for_list()
        return Region.objects.for_detail()

    @action(
        detail=True,
        methods=["GET"],
    )
    def get_regions_by_country_alpha_2(self, request, pk=None, *args, **kwargs):
        regions = Region.objects.for_list().filter(country__alpha_2=pk)
        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(regions, many=True)
        return Response(response_serializer.data, status=status.HTTP_200_OK)
