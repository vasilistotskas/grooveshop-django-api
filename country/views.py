from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema_view
from rest_framework.filters import SearchFilter

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods
from country.filters import CountryFilter
from country.models import Country
from country.serializers import (
    CountryDetailSerializer,
    CountrySerializer,
    CountryWriteSerializer,
)


@extend_schema_view(
    **create_schema_view_config(
        model_class=Country,
        display_config={
            "tag": "Countries",
        },
        serializers={
            "list_serializer": CountrySerializer,
            "detail_serializer": CountryDetailSerializer,
            "write_serializer": CountryWriteSerializer,
        },
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class CountryViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = Country.objects.all()
    serializers = {
        "default": CountryDetailSerializer,
        "list": CountrySerializer,
        "retrieve": CountryDetailSerializer,
        "create": CountryWriteSerializer,
        "update": CountryWriteSerializer,
        "partial_update": CountryWriteSerializer,
    }
    response_serializers = {
        "create": CountryDetailSerializer,
        "update": CountryDetailSerializer,
        "partial_update": CountryDetailSerializer,
    }
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = CountryFilter
    ordering_fields = [
        "alpha_2",
        "alpha_3",
        "iso_cc",
        "phone_code",
        "created_at",
        "updated_at",
        "sort_order",
        "translations__name",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "alpha_2",
        "alpha_3",
        "iso_cc",
        "phone_code",
        "translations__name",
    ]
