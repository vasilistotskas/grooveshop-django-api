from __future__ import annotations

from importlib import import_module

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from blog.models.post import BlogPost
from blog.serializers.comment import BlogCommentSerializer
from blog.serializers.post import BlogPostSerializer
from blog.strategies.related_posts_strategy import RelatedPostsStrategy
from blog.strategies.weighted_related_posts_strategy import WeightedRelatedPostsStrategy
from core.api.throttling import BurstRateThrottle
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from core.utils.views import cache_methods


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogPostViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = BlogPost.objects.all()
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "tags", "slug", "author"]
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "published_at",
    ]
    ordering = ["-created_at"]
    search_fields = ["id"]

    serializers = {
        "default": BlogPostSerializer,
        "comments": BlogCommentSerializer,
    }

    def get_related_posts_strategy(self) -> RelatedPostsStrategy:
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
        return WeightedRelatedPostsStrategy(strategies_with_weights, limit=limit)

    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAuthenticated],
        throttle_classes=[BurstRateThrottle],
    )
    def update_likes(self, request, pk=None) -> Response:
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
        serializer = self.get_serializer(post, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[AllowAny],
    )
    def update_view_count(self, request, pk=None) -> Response:
        post = self.get_object()
        post.view_count += 1
        post.save()
        serializer = self.get_serializer(post)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["GET"],
        permission_classes=[AllowAny],
    )
    def comments(self, request, pk=None) -> Response:
        post: BlogPost = self.get_object()
        queryset = post.comments.all()
        parent_id = request.query_params.get("parent", None)
        if parent_id is not None:
            if parent_id.lower() == "none":
                queryset = queryset.filter(parent__isnull=True)
            else:
                queryset = queryset.filter(parent_id=parent_id)

        return self.paginate_and_serialize(queryset, request)

    @action(
        detail=False,
        methods=["POST"],
        permission_classes=[IsAuthenticated],
    )
    def liked_posts(self, request, *args, **kwargs) -> Response:
        user = request.user
        post_ids = request.data.get("post_ids", [])
        if not isinstance(post_ids, list):
            return Response(
                {"error": "post_ids must be a list."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        liked_post_ids = BlogPost.objects.filter(likes=user, id__in=post_ids).values_list("id", flat=True)

        return Response(list(liked_post_ids), status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["GET"],
        permission_classes=[AllowAny],
    )
    def related_posts(self, request, pk=None) -> Response:
        post = self.get_object()
        strategy = self.get_related_posts_strategy()
        related_posts = strategy.get_related_posts(post)

        serializer = self.get_serializer(related_posts, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
