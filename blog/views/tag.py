from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.filters import SearchFilter

from blog.models.tag import BlogTag
from blog.serializers.tag import (
    BlogTagDetailSerializer,
    BlogTagListSerializer,
    BlogTagWriteSerializer,
)
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from core.utils.views import cache_methods


@extend_schema_view(
    list=extend_schema(
        summary=_("List blog tags"),
        description=_(
            "Retrieve a list of blog tags with filtering and search capabilities. "
            "Includes usage statistics and trending information."
        ),
        tags=["Blog Tags"],
        responses={
            200: BlogTagListSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a blog tag"),
        description=_("Create a new blog tag. Requires authentication."),
        tags=["Blog Tags"],
        request=BlogTagWriteSerializer,
        responses={
            201: BlogTagDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a blog tag"),
        description=_(
            "Get detailed information about a specific blog tag including "
            "related posts, usage statistics, and performance metrics."
        ),
        tags=["Blog Tags"],
        responses={
            200: BlogTagDetailSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a blog tag"),
        description=_("Update blog tag information. Requires authentication."),
        tags=["Blog Tags"],
        request=BlogTagWriteSerializer,
        responses={
            200: BlogTagDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a blog tag"),
        description=_(
            "Partially update blog tag information. Requires authentication."
        ),
        tags=["Blog Tags"],
        request=BlogTagWriteSerializer,
        responses={
            200: BlogTagDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a blog tag"),
        description=_("Delete a blog tag. Requires authentication."),
        tags=["Blog Tags"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogTagViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = BlogTag.objects.all()
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "active"]
    ordering_fields = ["id", "active", "created_at", "sort_order"]
    ordering = ["-created_at"]
    search_fields = ["translations__name"]
    serializers = {
        "list": BlogTagListSerializer,
        "retrieve": BlogTagDetailSerializer,
        "create": BlogTagWriteSerializer,
        "update": BlogTagWriteSerializer,
        "partial_update": BlogTagWriteSerializer,
    }
