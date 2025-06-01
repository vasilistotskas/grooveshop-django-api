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
from core.utils.views import cache_methods
from region.models import Region
from region.serializers import RegionSerializer


@extend_schema_view(
    list=extend_schema(
        summary=_("List regions"),
        description=_(
            "Retrieve a list of regions with filtering and search capabilities."
        ),
        tags=["Regions"],
        responses={
            200: RegionSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a region"),
        description=_("Create a new region. Requires authentication."),
        tags=["Regions"],
        responses={
            201: RegionSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a region"),
        description=_("Get detailed information about a specific region."),
        tags=["Regions"],
        responses={
            200: RegionSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a region"),
        description=_("Update region information. Requires authentication."),
        tags=["Regions"],
        responses={
            200: RegionSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a region"),
        description=_(
            "Partially update region information. Requires authentication."
        ),
        tags=["Regions"],
        responses={
            200: RegionSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a region"),
        description=_("Delete a region. Requires authentication."),
        tags=["Regions"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    get_regions_by_country_alpha_2=extend_schema(
        summary=_("Get regions by country"),
        description=_(
            "Get all regions for a specific country using the country's ISO alpha-2 code."
        ),
        tags=["Regions"],
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
    def get_regions_by_country_alpha_2(self, request, pk=None, *args, **kwargs):
        regions = Region.objects.filter(country__alpha_2=pk)
        serializer = self.get_serializer(regions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
