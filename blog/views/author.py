from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
)
from rest_framework.decorators import action

from blog.filters.author import BlogAuthorFilter
from blog.filters.post import BlogPostFilter
from blog.models.author import BlogAuthor
from blog.serializers.author import (
    BlogAuthorDetailSerializer,
    BlogAuthorSerializer,
    BlogAuthorWriteSerializer,
)
from blog.serializers.post import BlogPostSerializer
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods

req_serializers: RequestSerializersConfig = {
    "create": BlogAuthorWriteSerializer,
    "update": BlogAuthorWriteSerializer,
    "partial_update": BlogAuthorWriteSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": BlogAuthorDetailSerializer,
    "list": BlogAuthorSerializer,
    "retrieve": BlogAuthorDetailSerializer,
    "update": BlogAuthorDetailSerializer,
    "partial_update": BlogAuthorDetailSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=BlogAuthor,
        display_config={
            "tag": "Blog Authors",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
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

    def get_filterset_class(self):
        if self.action == "posts":
            return BlogPostFilter
        return BlogAuthorFilter

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return BlogAuthor.objects.none()

        if self.action == "posts":
            from rest_framework.generics import get_object_or_404

            author = get_object_or_404(BlogAuthor, id=self.kwargs["pk"])
            return author.blog_posts.all()

        return BlogAuthor.objects.all()

    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "user__first_name",
        "user__last_name",
        "user__email",
        "user__created_at",
        "website",
    ]
    ordering = ["-created_at", "user__first_name", "user__last_name"]
    search_fields = [
        "user__first_name",
        "user__last_name",
        "user__email",
        "user__username",
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
        parameters=[
            OpenApiParameter(
                name="ordering",
                type=str,
                description=_(
                    "Which field to use when ordering the results. Available fields: createdAt, updatedAt, publishedAt, title, viewCount, -createdAt, -updatedAt, -publishedAt, -title, -viewCount"
                ),
                enum=[
                    "createdAt",
                    "updatedAt",
                    "publishedAt",
                    "title",
                    "viewCount",
                    "-createdAt",
                    "-updatedAt",
                    "-publishedAt",
                    "-title",
                    "-viewCount",
                ],
            ),
        ],
        responses={
            200: BlogPostSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=["GET"])
    def posts(self, request, pk=None, *args, **kwargs):
        self.ordering_fields = [
            "created_at",
            "updated_at",
            "published_at",
            "title",
            "view_count",
        ]
        self.ordering = ["-published_at", "-created_at"]
        self.search_fields = [
            "translations__title",
            "translations__subtitle",
            "translations__body",
        ]
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)
