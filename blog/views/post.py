from __future__ import annotations

from datetime import timedelta
from functools import cached_property
from importlib import import_module
from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import F
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from blog.filters.post import BlogPostFilter
from blog.models.post import BlogPost
from blog.serializers.category import BlogCategorySerializer
from blog.serializers.comment import BlogCommentSerializer
from blog.serializers.post import BlogPostSerializer
from blog.strategies.weighted_related_posts_strategy import (
    WeightedRelatedPostsStrategy,
)
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from core.utils.views import cache_methods

if TYPE_CHECKING:
    from blog.strategies.related_posts_strategy import RelatedPostsStrategy


@extend_schema_view(
    list=extend_schema(
        summary=_("List blog posts"),
        description=_(
            "Retrieve a list of blog posts with rich filtering and search capabilities. "
            "Supports filtering by category, tags, author, engagement metrics, and content. "
            "Includes MeiliSearch integration for advanced full-text search."
        ),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a blog post"),
        description=_("Create a new blog post. Requires authentication."),
        tags=["Blog Posts"],
        responses={
            201: BlogPostSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a blog post"),
        description=_(
            "Get detailed information about a specific blog post including "
            "all relationships, engagement metrics, and SEO data."
        ),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a blog post"),
        description=_("Update blog post information. Requires authentication."),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a blog post"),
        description=_(
            "Partially update blog post information. Requires authentication."
        ),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a blog post"),
        description=_("Delete a blog post. Requires authentication."),
        tags=["Blog Posts"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update_likes=extend_schema(
        summary=_("Toggle post like"),
        description=_("Like or unlike a blog post. Toggles the like status."),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update_view_count=extend_schema(
        summary=_("Increment post view count"),
        description=_("Increment the view count for a blog post."),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    comments=extend_schema(
        summary=_("Get post comments"),
        description=_("Get all comments for a blog post."),
        tags=["Blog Posts"],
        responses={
            200: BlogCommentSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    liked_posts=extend_schema(
        summary=_("Get liked posts"),
        description=_("Get all posts that the authenticated user has liked."),
        tags=["Blog Posts"],
        request=inline_serializer(
            name="BlogPostLikedPostsRequest",
            fields={
                "post_ids": serializers.ListField(
                    child=serializers.IntegerField()
                )
            },
        ),
        responses={
            200: inline_serializer(
                name="LikedPostsResponse",
                fields={
                    "post_ids": serializers.ListField(
                        child=serializers.IntegerField()
                    )
                },
            ),
            400: ErrorResponseSerializer,
        },
    ),
    related_posts=extend_schema(
        summary=_("Get related posts"),
        description=_("Get related posts for a blog post."),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    trending=extend_schema(
        summary=_("Get trending posts"),
        description=_(
            "Get trending blog posts based on recent engagement metrics. "
            "Combines views, likes, and comments from recent time period."
        ),
        tags=["Blog Posts"],
        parameters=[
            OpenApiParameter(
                name="days",
                type=int,
                location=OpenApiParameter.QUERY,
                description="Number of days to look back for trending calculation",
                required=False,
                default=7,
            ),
        ],
        responses={
            200: BlogPostSerializer(many=True),
        },
    ),
    popular=extend_schema(
        summary=_("Get popular posts"),
        description=_(
            "Get most popular blog posts based on all-time engagement metrics."
        ),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer(many=True),
        },
    ),
    featured=extend_schema(
        summary=_("Get featured posts"),
        description=_(
            "Get posts marked as featured, ordered by publication date."
        ),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer(many=True),
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogPostViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = (
        BlogPost.objects.select_related("author", "category")
        .prefetch_related("tags", "likes")
        .all()
    )
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "published_at",
        "view_count",
        "likes_count",
        "comments_count",
        "tags_count",
        "featured",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "translations__title",
        "translations__description",
        "category__translations__name",
        "tags__translations__name",
    ]
    filterset_class = BlogPostFilter

    serializers = {
        "default": BlogPostSerializer,
        "comments": BlogCommentSerializer,
        "category": BlogCategorySerializer,
    }

    def get_queryset(self):
        queryset = (
            BlogPost.objects.select_related("author__user", "category")
            .prefetch_related(
                "tags__translations",
                "likes",
                "comments",
                "translations",
                "category__translations",
            )
            .with_all_annotations()
        )
        return queryset.with_all_annotations()

    @cached_property
    def related_posts_strategy(self):
        strategies_with_weights: list[tuple[RelatedPostsStrategy, float]] = []
        for strategy_config in settings.RELATED_POSTS_STRATEGIES:
            strategy_path = strategy_config["strategy"]
            weight = strategy_config["weight"]
            module_path, class_name = strategy_path.rsplit(".", 1)
            module = import_module(module_path)
            strategy_class = getattr(module, class_name)
            strategy_instance = strategy_class()
            strategies_with_weights.append((strategy_instance, weight))

        limit = getattr(settings, "RELATED_POSTS_LIMIT", 8)
        return WeightedRelatedPostsStrategy(
            strategies_with_weights, limit=limit
        )

    def get_related_posts_strategy(self):
        return self.related_posts_strategy

    @action(detail=True, methods=["POST"])
    def update_likes(self, request, pk=None):
        if not request.user.is_authenticated:
            return Response(
                {"detail": _("Authentication credentials were not provided.")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        post = self.get_object()
        user = request.user

        if post.likes.filter(pk=user.pk).exists():
            post.likes.remove(user)
        else:
            post.likes.add(user)
        post.save()
        serializer = self.get_serializer(
            post, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["POST"])
    def update_view_count(self, request, pk=None):
        post = self.get_object()
        post.view_count += 1
        post.save(update_fields=["view_count"])
        serializer = self.get_serializer(post)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["GET"])
    def comments(self, request, pk=None):
        post = self.get_object()
        queryset = post.comments.select_related(
            "user", "parent"
        ).prefetch_related("translations", "likes")

        parent_id = request.query_params.get("parent", None)
        if parent_id is not None:
            if parent_id.lower() == "none":
                queryset = queryset.filter(parent__isnull=True)
            else:
                try:
                    queryset = queryset.filter(parent_id=int(parent_id))
                except (ValueError, TypeError):
                    return Response(
                        {"detail": _("Invalid parent ID.")},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"])
    def related_posts(self, request, pk=None):
        post = self.get_object()
        strategy = self.get_related_posts_strategy()
        related_posts = strategy.get_related_posts(post)

        serializer = self.get_serializer(related_posts, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def liked_posts(self, request, *args, **kwargs):
        user = request.user
        post_ids = request.data.get("post_ids", [])
        if not isinstance(post_ids, list):
            return Response(
                {"error": _("post_ids must be a list.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        liked_post_ids = BlogPost.objects.filter(
            likes=user, id__in=post_ids
        ).values_list("id", flat=True)

        return Response(list(liked_post_ids), status=status.HTTP_200_OK)

    @action(detail=False, methods=["GET"])
    def trending(self, request):
        from django.utils import timezone

        days = int(request.query_params.get("days", 7))
        cutoff_date = timezone.now() - timedelta(days=days)

        queryset = (
            self.get_queryset()
            .filter(published_at__gte=cutoff_date)
            .annotate(
                trending_score=(
                    F("view_count")
                    + (F("likes_count_field") * 2)
                    + (F("comments_count_field") * 3)
                )
            )
            .order_by("-trending_score")
        )

        return self.paginate_and_serialize(queryset, request)

    @action(detail=False, methods=["GET"])
    def popular(self, request):
        queryset = self.get_queryset().order_by(
            "-likes_count_field", "-view_count", "-comments_count_field"
        )

        return self.paginate_and_serialize(queryset, request)

    @action(detail=False, methods=["GET"])
    def featured(self, request):
        queryset = (
            self.get_queryset().filter(featured=True).order_by("-published_at")
        )

        return self.paginate_and_serialize(queryset, request)
