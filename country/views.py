from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.filters import SearchFilter

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from core.utils.views import cache_methods
from country.models import Country
from country.serializers import (
    CountryDetailSerializer,
    CountryListSerializer,
    CountryWriteSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary=_("List countries"),
        description=_(
            "Retrieve a list of countries with filtering and search capabilities."
        ),
        tags=["Countries"],
        responses={
            200: CountryListSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a country"),
        description=_("Create a new country record. Requires authentication."),
        tags=["Countries"],
        responses={
            201: CountryDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a country"),
        description=_("Get detailed information about a specific country."),
        tags=["Countries"],
        responses={
            200: CountryDetailSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a country"),
        description=_("Update country information. Requires authentication."),
        tags=["Countries"],
        request=CountryWriteSerializer,
        responses={
            200: CountryDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a country"),
        description=_(
            "Partially update country information. Requires authentication."
        ),
        tags=["Countries"],
        request=CountryWriteSerializer,
        responses={
            200: CountryDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a country"),
        description=_("Delete a country record. Requires authentication."),
        tags=["Countries"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class CountryViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = Country.objects.all()
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = [
        "alpha_2",
        "alpha_3",
        "translations__name",
        "iso_cc",
        "phone_code",
    ]
    ordering_fields = ["alpha_2", "created_at"]
    ordering = ["-created_at"]
    search_fields = [
        "alpha_2",
        "alpha_3",
        "iso_cc",
        "phone_code",
    ]
    serializers = {
        "list": CountryListSerializer,
        "retrieve": CountryDetailSerializer,
        "create": CountryWriteSerializer,
        "update": CountryWriteSerializer,
        "partial_update": CountryWriteSerializer,
    }
