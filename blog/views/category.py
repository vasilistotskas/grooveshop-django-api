from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from blog.filters.category import BlogCategoryFilter
from blog.models.category import BlogCategory
from blog.serializers.category import (
    BlogCategoryDetailSerializer,
    BlogCategoryReorderRequestSerializer,
    BlogCategoryReorderResponseSerializer,
    BlogCategorySerializer,
    BlogCategoryWriteSerializer,
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
        model_class=BlogCategory,
        display_config={
            "tag": "Blog Categories",
        },
        serializers={
            "list_serializer": BlogCategorySerializer,
            "detail_serializer": BlogCategoryDetailSerializer,
            "write_serializer": BlogCategoryWriteSerializer,
        },
    ),
    posts=extend_schema(
        operation_id="listBlogCategoryPosts",
        summary=_("Get category's blog posts"),
        description=_(
            "Retrieve all blog posts in this category and its subcategories. "
            "Use 'recursive=true' to include posts from all descendant categories."
        ),
        tags=["Blog Categories"],
        parameters=[
            OpenApiParameter(
                name="recursive",
                type=bool,
                location=OpenApiParameter.QUERY,
                description="Include posts from all descendant categories",
                required=False,
                default=False,
            ),
        ],
        responses={
            200: BlogPostSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    children=extend_schema(
        operation_id="listBlogCategoryChildren",
        summary=_("Get category children"),
        description=_("Get direct children of this category."),
        tags=["Blog Categories"],
        responses={
            200: BlogCategorySerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    descendants=extend_schema(
        operation_id="listBlogCategoryDescendants",
        summary=_("Get category descendants"),
        description=_(
            "Get all descendants (children, grandchildren, etc.) of this category."
        ),
        tags=["Blog Categories"],
        responses={
            200: BlogCategorySerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    ancestors=extend_schema(
        operation_id="listBlogCategoryAncestors",
        summary=_("Get category ancestors"),
        description=_(
            "Get all ancestors (parent, grandparent, etc.) of this category."
        ),
        tags=["Blog Categories"],
        responses={
            200: BlogCategorySerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    siblings=extend_schema(
        operation_id="listBlogCategorySiblings",
        summary=_("Get category siblings"),
        description=_("Get sibling categories (same parent level)."),
        tags=["Blog Categories"],
        responses={
            200: BlogCategorySerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    tree=extend_schema(
        operation_id="getBlogCategoryTree",
        summary=_("Get complete category tree"),
        description=_(
            "Get the complete category tree structure with nested relationships. "
            "This is more efficient than using list?tree=true for displaying "
            "navigation menus or category hierarchies."
        ),
        tags=["Blog Categories"],
        responses={
            200: BlogCategorySerializer(many=True),
        },
    ),
    reorder=extend_schema(
        operation_id="reorderBlogCategories",
        summary=_("Reorder categories"),
        description=_(
            "Batch reorder categories by updating their sort_order values."
        ),
        tags=["Blog Categories"],
        request=BlogCategoryReorderRequestSerializer,
        responses={
            200: BlogCategoryReorderResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(
    settings.DEFAULT_CACHE_TTL,
    methods=["list", "retrieve", "posts", "tree", "ancestors", "descendants"],
)
class BlogCategoryViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = BlogCategory.objects.all()
    serializers = {
        "default": BlogCategoryDetailSerializer,
        "list": BlogCategorySerializer,
        "retrieve": BlogCategoryDetailSerializer,
        "create": BlogCategoryWriteSerializer,
        "update": BlogCategoryWriteSerializer,
        "partial_update": BlogCategoryWriteSerializer,
        "posts": BlogPostSerializer,
        "children": BlogCategorySerializer,
        "descendants": BlogCategorySerializer,
        "ancestors": BlogCategorySerializer,
        "siblings": BlogCategorySerializer,
        "tree": BlogCategorySerializer,
        "reorder": BlogCategoryReorderResponseSerializer,
    }
    response_serializers = {
        "create": BlogCategoryDetailSerializer,
        "update": BlogCategoryDetailSerializer,
        "partial_update": BlogCategoryDetailSerializer,
    }
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = BlogCategoryFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "sort_order",
        "level",
        "lft",
        "rght",
    ]
    ordering = ["sort_order", "lft", "-created_at"]
    search_fields = [
        "slug",
        "translations__name",
        "translations__description",
    ]

    def get_queryset(self):
        if self.request and self.request.query_params.get("tree") == "true":
            return BlogCategory.objects.select_related(
                "parent"
            ).prefetch_related(
                "translations",
                "children",
                "blog_posts",
            )

        return BlogCategory.objects.select_related("parent").prefetch_related(
            "translations",
            "blog_posts",
        )

    def list(self, request, *args, **kwargs):
        if request.query_params.get("tree") == "true":
            return self.get_tree_structure(request)
        return super().list(request, *args, **kwargs)

    def get_tree_structure(self, request):
        queryset = self.filter_queryset(
            self.get_queryset().filter(parent__isnull=True)
        )

        serializer = BlogCategorySerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["GET"])
    def posts(self, request, pk=None, *args, **kwargs):
        category = self.get_object()

        self.ordering_fields = [
            "created_at",
            "updated_at",
            "published_at",
            "title",
            "views_count",
        ]

        if request.query_params.get("recursive") == "true":
            categories = category.get_descendants(include_self=True)
            queryset = (
                category.__class__.objects.get(pk=pk)
                .blog_posts.model.objects.filter(category__in=categories)
                .select_related("category", "author__user")
                .prefetch_related("likes", "tags")
            )
        else:
            queryset = category.blog_posts.select_related(
                "author__user"
            ).prefetch_related("likes", "tags")

        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"])
    def children(self, request, pk=None):
        category = self.get_object()
        queryset = (
            category.get_children()
            .select_related("parent")
            .prefetch_related("translations", "blog_posts")
        )
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"])
    def descendants(self, request, pk=None):
        category = self.get_object()
        queryset = (
            category.get_descendants()
            .select_related("parent")
            .prefetch_related("translations", "blog_posts")
        )
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"])
    def ancestors(self, request, pk=None):
        category = self.get_object()
        queryset = (
            category.get_ancestors()
            .select_related("parent")
            .prefetch_related("translations")
        )
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"])
    def siblings(self, request, pk=None):
        category = self.get_object()
        queryset = (
            category.get_siblings()
            .select_related("parent")
            .prefetch_related("translations", "blog_posts")
        )
        return self.paginate_and_serialize(queryset, request)

    @action(detail=False, methods=["GET"])
    def tree(self, request):
        queryset = (
            BlogCategory.objects.filter(parent__isnull=True)
            .select_related("parent")
            .prefetch_related(
                "translations",
                "children__translations",
                "children__children__translations",
                "blog_posts",
            )
        )
        return self.paginate_and_serialize(queryset, request)

    @action(detail=False, methods=["POST"])
    def reorder(self, request):
        serializer = BlogCategoryReorderRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        categories_data = serializer.validated_data["categories"]
        updated_count = 0

        for category_data in categories_data:
            try:
                category = BlogCategory.objects.get(id=category_data["id"])
                category.sort_order = category_data["sort_order"]
                category.save(update_fields=["sort_order"])
                updated_count += 1
            except BlogCategory.DoesNotExist:
                continue

        response_data = {
            "updated_count": updated_count,
            "message": _("Categories reordered successfully."),
        }

        response_serializer = BlogCategoryReorderResponseSerializer(
            response_data
        )
        return Response(response_serializer.data)
