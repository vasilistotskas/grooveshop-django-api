from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import cache_methods
from region.models import Region
from region.serializers import RegionSerializer


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve", "get_regions_by_country_alpha_2"])
class RegionViewSet(BaseModelViewSet):
    queryset = Region.objects.all()
    serializer_class = RegionSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["alpha", "country"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]
    search_fields = ["alpha", "country"]

    @action(
        detail=True,
        methods=["GET"],
    )
    def get_regions_by_country_alpha_2(self, request, pk=None, *args, **kwargs) -> Response:
        regions = Region.objects.filter(country__alpha_2=pk)
        serializer = self.get_serializer(regions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
