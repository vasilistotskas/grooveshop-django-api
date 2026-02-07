from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
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
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from core.utils.views import cache_methods

serializers_config: SerializersConfig = {
    **crud_config(
        list=BlogAuthorSerializer,
        detail=BlogAuthorDetailSerializer,
        write=BlogAuthorWriteSerializer,
    ),
    "posts": ActionConfig(
        response=BlogPostSerializer,
        many=True,
        operation_id="getBlogAuthorPosts",
        summary=_("Get author's blog posts"),
        description=_(
            "Retrieve all blog posts written by this author with proper pagination."
        ),
        tags=["Blog Authors"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=BlogAuthor,
        display_config={
            "tag": "Blog Authors",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(
    settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve", "posts"]
)
class BlogAuthorViewSet(BaseModelViewSet):
    queryset = BlogAuthor.objects.none()
    serializers_config = serializers_config

    def get_filterset_class(self):
        if self.action == "posts":
            return BlogPostFilter
        return BlogAuthorFilter

    def get_queryset(self):
        if self.action == "posts":
            from rest_framework.generics import get_object_or_404

            author = get_object_or_404(BlogAuthor, id=self.kwargs["pk"])
            return author.blog_posts.select_related(
                "category", "author__user"
            ).prefetch_related("translations", "tags__translations", "likes")

        if self.action == "list":
            return BlogAuthor.objects.for_list()
        return BlogAuthor.objects.for_detail()

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

    @action(detail=True, methods=["GET"])
    def posts(self, request, pk=None, *args, **kwargs):
        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)
