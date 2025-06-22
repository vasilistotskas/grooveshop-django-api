from __future__ import annotations

from datetime import timedelta
from functools import cached_property
from importlib import import_module
from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from blog.filters.post import BlogPostFilter
from blog.models.post import BlogPost
from blog.serializers.comment import BlogCommentSerializer
from blog.serializers.post import (
    BlogPostDetailSerializer,
    BlogPostLikedPostsRequestSerializer,
    BlogPostLikedPostsResponseSerializer,
    BlogPostSerializer,
    BlogPostWriteSerializer,
)
from blog.strategies.weighted_related_posts_strategy import (
    WeightedRelatedPostsStrategy,
)
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods

if TYPE_CHECKING:
    from blog.strategies.related_posts_strategy import RelatedPostsStrategy


@extend_schema_view(
    **create_schema_view_config(
        model_class=BlogPost,
        display_config={
            "tag": "Blog Post",
        },
        serializers={
            "list_serializer": BlogPostSerializer,
            "detail_serializer": BlogPostDetailSerializer,
            "write_serializer": BlogPostWriteSerializer,
        },
        error_serializer=ErrorResponseSerializer,
        additional_responses={
            "create": {201: BlogPostDetailSerializer},
            "update": {200: BlogPostDetailSerializer},
            "partial_update": {200: BlogPostDetailSerializer},
        },
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogPostViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = BlogPost.objects.all()
    serializers = {
        "default": BlogPostDetailSerializer,
        "list": BlogPostSerializer,
        "retrieve": BlogPostDetailSerializer,
        "create": BlogPostWriteSerializer,
        "update": BlogPostWriteSerializer,
        "partial_update": BlogPostWriteSerializer,
        "update_likes": BlogPostLikedPostsResponseSerializer,
        "update_view_count": BlogPostDetailSerializer,
        "liked_posts": BlogPostLikedPostsResponseSerializer,
        "related_posts": BlogPostSerializer,
        "comments": BlogCommentSerializer,
        "trending": BlogPostSerializer,
        "popular": BlogPostSerializer,
        "featured": BlogPostSerializer,
    }
    response_serializers = {
        "create": BlogPostDetailSerializer,
        "update": BlogPostDetailSerializer,
        "partial_update": BlogPostDetailSerializer,
    }
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = BlogPostFilter
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
        "translations__subtitle",
        "translations__body",
        "category__translations__name",
        "tags__translations__name",
    ]

    def get_queryset(self):
        queryset = (
            BlogPost.objects.select_related(
                "author__user", "category", "author"
            )
            .prefetch_related(
                "tags__translations",
                "likes",
                "comments",
                "translations",
                "category__translations",
            )
            .with_all_annotations()
        )
        return queryset

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

    @extend_schema(
        operation_id="toggleBlogPostLike",
        summary=_("Toggle post like"),
        description=_("Like or unlike a blog post. Toggles the like status."),
        tags=["Blog Posts"],
        request=BlogPostLikedPostsRequestSerializer,
        responses={
            200: BlogPostLikedPostsResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    )
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

        serializer = BlogPostDetailSerializer(
            post, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="incrementBlogPostViews",
        summary=_("Increment post view count"),
        description=_("Increment the view count for a blog post."),
        tags=["Blog Posts"],
        responses={
            200: BlogPostDetailSerializer,
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=["POST"])
    def update_view_count(self, request, pk=None):
        post = self.get_object()
        post.view_count += 1
        post.save(update_fields=["view_count"])
        serializer = self.get_serializer(post)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="listBlogPostRelated",
        summary=_("Get related posts"),
        description=_("Get related posts for a blog post."),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=["GET"])
    def related_posts(self, request, pk=None):
        post = self.get_object()
        strategy = self.get_related_posts_strategy()
        related_posts = strategy.get_related_posts(post)

        serializer = self.get_serializer(related_posts, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="checkBlogPostLikes",
        summary=_("Get liked posts"),
        description=_("Get all posts that the authenticated user has liked."),
        tags=["Blog Posts"],
        request=BlogPostLikedPostsRequestSerializer,
        responses={
            200: BlogPostLikedPostsResponseSerializer,
            400: ErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["POST"])
    def liked_posts(self, request, *args, **kwargs):
        serializer = BlogPostLikedPostsRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                serializer.errors, status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user
        post_ids = serializer.validated_data["post_ids"]

        liked_post_ids = BlogPost.objects.filter(
            likes=user, id__in=post_ids
        ).values_list("id", flat=True)

        response_data = {"post_ids": list(liked_post_ids)}
        response_serializer = BlogPostLikedPostsResponseSerializer(
            response_data
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="listBlogPostComments",
        summary=_("Get post comments"),
        description=_("Get all comments for a blog post."),
        tags=["Blog Posts"],
        responses={
            200: BlogCommentSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
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

    @extend_schema(
        operation_id="listTrendingBlogPosts",
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
    )
    @action(detail=False, methods=["GET"])
    def trending(self, request):
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

    @extend_schema(
        operation_id="listPopularBlogPosts",
        summary=_("Get popular posts"),
        description=_(
            "Get most popular blog posts based on all-time engagement metrics."
        ),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer(many=True),
        },
    )
    @action(detail=False, methods=["GET"])
    def popular(self, request):
        queryset = self.get_queryset().order_by(
            "-likes_count_field", "-view_count"
        )
        return self.paginate_and_serialize(queryset, request)

    @extend_schema(
        operation_id="listFeaturedBlogPosts",
        summary=_("Get featured posts"),
        description=_(
            "Get posts marked as featured, ordered by publication date."
        ),
        tags=["Blog Posts"],
        responses={
            200: BlogPostSerializer(many=True),
        },
    )
    @action(detail=False, methods=["GET"])
    def featured(self, request):
        queryset = (
            self.get_queryset().filter(featured=True).order_by("-published_at")
        )
        return self.paginate_and_serialize(queryset, request)
