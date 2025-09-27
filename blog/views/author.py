from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
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
    "posts": BlogPostSerializer,
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
class BlogAuthorViewSet(BaseModelViewSet):
    queryset = BlogAuthor.objects.none()
    response_serializers = res_serializers
    request_serializers = req_serializers

    def get_filterset_class(self):
        if self.action == "posts":
            return BlogPostFilter
        return BlogAuthorFilter

    def get_queryset(self):
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
        responses={
            200: BlogPostSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=["GET"])
    def posts(self, request, pk=None, *args, **kwargs):
        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)
