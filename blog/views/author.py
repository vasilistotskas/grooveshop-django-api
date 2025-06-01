from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from blog.filters.author import BlogAuthorFilter
from blog.models.author import BlogAuthor
from blog.serializers.author import BlogAuthorSerializer
from blog.serializers.post import BlogPostSerializer
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
            200: BlogAuthorSerializer(many=True),
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a blog author"),
        description=_("Get detailed information about a specific blog author."),
        tags=["Blog Authors"],
        responses={
            200: BlogAuthorSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    create=extend_schema(
        summary=_("Create a blog author"),
        description=_(
            "Create a new blog author profile. Requires authentication."
        ),
        tags=["Blog Authors"],
        responses={
            201: BlogAuthorSerializer,
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
        responses={
            200: BlogAuthorSerializer,
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
        responses={
            200: BlogAuthorSerializer,
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
            200: BlogPostSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    stats=extend_schema(
        summary=_("Get author statistics"),
        description=_(
            "Get comprehensive statistics about the author's blog posts and activity."
        ),
        tags=["Blog Authors"],
        responses={
            200: inline_serializer(
                name="BlogAuthorStatsResponse",
                fields={
                    "total_posts": serializers.IntegerField(),
                    "total_likes_received": serializers.IntegerField(),
                    "member_since": serializers.DateField(),
                    "has_website": serializers.BooleanField(),
                    "full_name": serializers.CharField(),
                    "user_active": serializers.BooleanField(),
                    "posts_this_year": serializers.IntegerField(),
                    "average_likes_per_post": serializers.FloatField(),
                },
            ),
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogAuthorViewSet(MultiSerializerMixin, BaseModelViewSet):
    serializers = {
        "default": BlogAuthorSerializer,
        "list": BlogAuthorSerializer,
        "retrieve": BlogAuthorSerializer,
        "create": BlogAuthorSerializer,
        "update": BlogAuthorSerializer,
        "partial_update": BlogAuthorSerializer,
        "destroy": BlogAuthorSerializer,
        "posts": BlogPostSerializer,
    }
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

    @action(detail=True, methods=["GET"])
    def stats(self, request, pk=None):
        author = self.get_object()

        posts_this_year = author.blog_posts.filter(
            created_at__year=request.user.date_joined.year
            if hasattr(request.user, "date_joined")
            else 2024
        ).count()

        avg_likes = 0
        if author.number_of_posts > 0:
            avg_likes = author.total_likes_received / author.number_of_posts

        stats = {
            "total_posts": author.number_of_posts,
            "total_likes_received": author.total_likes_received,
            "member_since": author.created_at.date(),
            "has_website": bool(author.website),
            "full_name": author.full_name,
            "user_active": author.user.is_active,
            "posts_this_year": posts_this_year,
            "average_likes_per_post": round(avg_likes, 2),
        }

        return Response(stats)
