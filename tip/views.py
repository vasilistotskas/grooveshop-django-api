from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.filters import SearchFilter

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import cache_methods
from tip.models import Tip
from tip.serializers import TipSerializer


@extend_schema_view(
    list=extend_schema(
        summary=_("List tips"),
        description=_(
            "Retrieve a list of tips with filtering and search capabilities."
        ),
        tags=["Tips"],
        responses={
            200: TipSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a tip"),
        description=_("Create a new tip. Requires authentication."),
        tags=["Tips"],
        responses={
            201: TipSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a tip"),
        description=_("Get detailed information about a specific tip."),
        tags=["Tips"],
        responses={
            200: TipSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a tip"),
        description=_("Update tip information. Requires authentication."),
        tags=["Tips"],
        responses={
            200: TipSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a tip"),
        description=_(
            "Partially update tip information. Requires authentication."
        ),
        tags=["Tips"],
        responses={
            200: TipSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a tip"),
        description=_("Delete a tip. Requires authentication."),
        tags=["Tips"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class TipViewSet(BaseModelViewSet):
    queryset = Tip.objects.all()
    serializer_class = TipSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "kind", "active"]
    ordering_fields = ["id", "kind", "active", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id", "translations__title"]
