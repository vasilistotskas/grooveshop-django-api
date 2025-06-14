from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
)
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter

from blog.filters.author import BlogAuthorFilter
from blog.models.author import BlogAuthor
from blog.serializers.author import (
    BlogAuthorDetailSerializer,
    BlogAuthorListSerializer,
    BlogAuthorWriteSerializer,
)
from blog.serializers.post import BlogPostListSerializer
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from core.utils.views import cache_methods


@extend_schema_view(
    list=extend_schema(
        summary=_("List blog authors"),
        description=_(
            "Retrieve a list of all blog authors with their basic information. "
            "Supports advanced filtering including activity levels, post counts, and more."
        ),
        tags=["Blog Authors"],
        responses={
            200: BlogAuthorListSerializer(many=True),
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a blog author"),
        description=_("Get detailed information about a specific blog author."),
        tags=["Blog Authors"],
        responses={
            200: BlogAuthorDetailSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    create=extend_schema(
        summary=_("Create a blog author"),
        description=_(
            "Create a new blog author profile. Requires authentication."
        ),
        tags=["Blog Authors"],
        request=BlogAuthorWriteSerializer,
        responses={
            201: BlogAuthorDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a blog author"),
        description=_(
            "Update blog author information. Requires authentication."
        ),
        tags=["Blog Authors"],
        request=BlogAuthorWriteSerializer,
        responses={
            200: BlogAuthorDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a blog author"),
        description=_(
            "Partially update blog author information. Requires authentication."
        ),
        tags=["Blog Authors"],
        request=BlogAuthorWriteSerializer,
        responses={
            200: BlogAuthorDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a blog author"),
        description=_("Delete a blog author profile. Requires authentication."),
        tags=["Blog Authors"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    posts=extend_schema(
        summary=_("Get author's blog posts"),
        description=_(
            "Retrieve all blog posts written by this author with proper pagination."
        ),
        tags=["Blog Authors"],
        responses={
            200: BlogPostListSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogAuthorViewSet(MultiSerializerMixin, BaseModelViewSet):
    filterset_class = BlogAuthorFilter
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
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
    serializers = {
        "list": BlogAuthorListSerializer,
        "retrieve": BlogAuthorDetailSerializer,
        "create": BlogAuthorWriteSerializer,
        "update": BlogAuthorWriteSerializer,
        "partial_update": BlogAuthorWriteSerializer,
        "posts": BlogPostListSerializer,
    }

    def get_queryset(self):
        return BlogAuthor.objects.select_related("user").prefetch_related(
            "blog_posts", "blog_posts__likes"
        )

    @action(detail=True, methods=["GET"])
    def posts(self, request, pk=None, *args, **kwargs):
        self.ordering_fields = [
            "created_at",
            "updated_at",
            "title",
            "published_at",
        ]

        author = self.get_object()
        queryset = author.blog_posts.all()

        return self.paginate_and_serialize(queryset, request)
