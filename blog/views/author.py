from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from blog.filters.author import BlogAuthorFilter
from blog.models.author import BlogAuthor
from blog.serializers.author import (
    BlogAuthorDetailSerializer,
    BlogAuthorSerializer,
    BlogAuthorWriteSerializer,
)
from blog.serializers.post import BlogPostSerializer
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods


@extend_schema_view(
    **create_schema_view_config(
        model_class=BlogAuthor,
        display_config={
            "tag": "Blog Authors",
        },
        serializers={
            "list_serializer": BlogAuthorSerializer,
            "detail_serializer": BlogAuthorDetailSerializer,
            "write_serializer": BlogAuthorWriteSerializer,
        },
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogAuthorViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = BlogAuthor.objects.all()
    serializers = {
        "default": BlogAuthorDetailSerializer,
        "list": BlogAuthorSerializer,
        "retrieve": BlogAuthorDetailSerializer,
        "create": BlogAuthorWriteSerializer,
        "update": BlogAuthorWriteSerializer,
        "partial_update": BlogAuthorWriteSerializer,
        "posts": BlogPostSerializer,
    }
    response_serializers = {
        "create": BlogAuthorDetailSerializer,
        "update": BlogAuthorDetailSerializer,
        "partial_update": BlogAuthorDetailSerializer,
    }
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = BlogAuthorFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "user__first_name",
        "user__last_name",
        "user__date_joined",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "user__first_name",
        "user__last_name",
        "user__email",
        "website",
        "translations__bio",
    ]

    @extend_schema(
        operation_id="getBlogAuthorPosts",
        summary=_("Get author's blog posts"),
        description=_(
            "Retrieve all blog posts written by this author with proper pagination."
        ),
        tags=["Blog Authors"],
        responses={
            200: BlogPostSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=["GET"])
    def posts(self, request, pk=None, *args, **kwargs):
        author = self.get_object()
        queryset = author.blog_posts.all()

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = BlogPostSerializer(
                page, many=True, context=self.get_serializer_context()
            )
            return self.get_paginated_response(serializer.data)

        serializer = BlogPostSerializer(
            queryset, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data)
