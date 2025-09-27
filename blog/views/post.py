from __future__ import annotations

from datetime import timedelta
from functools import cached_property
from importlib import import_module
from typing import TYPE_CHECKING
from django.conf import settings
from django.db.models import F, Count, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.decorators import action

from rest_framework.response import Response

from blog.filters.comment import BlogCommentFilter
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

from core.utils.serializers import (
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods

if TYPE_CHECKING:
    from blog.strategies.related_posts_strategy import RelatedPostsStrategy

req_serializers: RequestSerializersConfig = {
    "create": BlogPostWriteSerializer,
    "update": BlogPostWriteSerializer,
    "partial_update": BlogPostWriteSerializer,
    "liked_posts": BlogPostLikedPostsRequestSerializer,
    "update_likes": None,
    "update_view_count": None,
}

res_serializers: ResponseSerializersConfig = {
    "create": BlogPostDetailSerializer,
    "list": BlogPostSerializer,
    "retrieve": BlogPostDetailSerializer,
    "update": BlogPostDetailSerializer,
    "partial_update": BlogPostDetailSerializer,
    "update_likes": BlogPostDetailSerializer,
    "update_view_count": BlogPostDetailSerializer,
    "related_posts": BlogPostSerializer,
    "liked_posts": BlogPostLikedPostsResponseSerializer,
    "comments": BlogCommentSerializer,
    "trending": BlogPostSerializer,
    "popular": BlogPostSerializer,
    "featured": BlogPostSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=BlogPost,
        display_config={
            "tag": "Blog Post",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogPostViewSet(BaseModelViewSet):
    queryset = BlogPost.objects.all()
    request_serializers = req_serializers
    response_serializers = res_serializers

    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "published_at",
        "view_count",
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
        queryset = BlogPost.objects.select_related(
            "author__user", "category", "author"
        ).prefetch_related(
            "tags__translations",
            "likes",
            "comments",
            "translations",
            "category__translations",
        )
        return queryset

    def get_filterset_class(self):
        if self.action == "comments":
            return None
        return BlogPostFilter

    @property
    def filterset_class(self):
        return self.get_filterset_class()

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
        request=None,
        responses={
            200: BlogPostDetailSerializer,
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

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            post, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

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

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            post, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="listBlogPostRelated",
        summary=_("Get related posts"),
        description=_("Get related posts for a blog post."),
        tags=["Blog Posts"],
        parameters=[
            OpenApiParameter(
                name="id",
                description="A unique integer value identifying this blog post.",
                required=True,
                type=int,
                location=OpenApiParameter.PATH,
            ),
        ],
        responses={
            200: {
                "type": "array",
                "items": {"$ref": "#/components/schemas/BlogPost"},
            },
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=["GET"])
    def related_posts(self, request, pk=None):
        post = self.get_object()
        strategy = self.get_related_posts_strategy()
        related_posts = strategy.get_related_posts(post)

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            related_posts, many=True, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

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
        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        if not request_serializer.is_valid():
            return Response(
                request_serializer.errors, status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user
        post_ids = request_serializer.validated_data["post_ids"]

        liked_post_ids = BlogPost.objects.filter(
            likes=user, id__in=post_ids
        ).values_list("id", flat=True)

        response_data = {"post_ids": list(liked_post_ids)}

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(response_data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="listBlogPostComments",
        summary=_("Get post comments"),
        description=_("Get all comments for a blog post."),
        tags=["Blog Posts"],
        parameters=[
            OpenApiParameter(
                name="parent",
                type=str,
                description="Parent comment ID",
                required=False,
            ),
            OpenApiParameter(
                name="parent__isnull",
                type=bool,
                description="Filter comments with no parent",
                required=False,
            ),
        ],
        responses={
            200: BlogCommentSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=["GET"])
    def comments(self, request, pk=None):
        post = self.get_object()

        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        queryset = post.comments.select_related(
            "user", "parent"
        ).prefetch_related("translations", "likes")

        # Filter out unapproved comments for non-staff users
        if not request.user.is_staff:
            queryset = queryset.filter(approved=True)

        comment_filterset = BlogCommentFilter(
            data=request.GET, queryset=queryset, request=request
        )

        if comment_filterset.is_valid():
            queryset = comment_filterset.qs
        else:
            return Response(
                {"filter_errors": comment_filterset.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

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
                likes_count_annotation=Count("likes", distinct=True),
                comments_count_annotation=Count(
                    "comments", distinct=True, filter=Q(comments__approved=True)
                ),
                trending_score=F("view_count")
                + (F("likes_count_annotation") * 2)
                + (F("comments_count_annotation") * 3),
            )
            .order_by("-trending_score")
        )

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

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
        queryset = (
            self.get_queryset()
            .annotate(likes_count_annotation=Count("likes", distinct=True))
            .order_by("-likes_count_annotation", "-view_count")
        )

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

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

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )
