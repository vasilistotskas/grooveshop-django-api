from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from blog.filters.category import BlogCategoryFilter
from blog.models.category import BlogCategory
from blog.serializers.category import BlogCategorySerializer
from blog.serializers.post import BlogPostSerializer
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from core.utils.views import cache_methods


@extend_schema_view(
    list=extend_schema(
        summary=_("List blog categories"),
        description=_(
            "Retrieve a list of blog categories with hierarchical support. "
            "Supports filtering by parent, level, and translation fields. "
            "Use 'tree=true' parameter to get nested tree structure."
        ),
        tags=["Blog Categories"],
        responses={
            200: BlogCategorySerializer(many=True),
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a blog category"),
        description=_(
            "Get detailed information about a specific blog category including "
            "ancestors, descendants, and post counts."
        ),
        tags=["Blog Categories"],
        responses={
            200: BlogCategorySerializer,
            404: ErrorResponseSerializer,
        },
    ),
    create=extend_schema(
        summary=_("Create a blog category"),
        description=_(
            "Create a new blog category. Supports hierarchical structure."
        ),
        tags=["Blog Categories"],
        responses={
            201: BlogCategorySerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a blog category"),
        description=_("Update blog category information."),
        tags=["Blog Categories"],
        responses={
            200: BlogCategorySerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a blog category"),
        description=_("Partially update blog category information."),
        tags=["Blog Categories"],
        responses={
            200: BlogCategorySerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a blog category"),
        description=_(
            "Delete a blog category. Note: This will also affect all child categories "
            "and associated blog posts based on cascade settings."
        ),
        tags=["Blog Categories"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    posts=extend_schema(
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
        summary=_("Get category children"),
        description=_("Get direct children of this category."),
        tags=["Blog Categories"],
        responses={
            200: BlogCategorySerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    descendants=extend_schema(
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
        summary=_("Get category siblings"),
        description=_("Get sibling categories (same parent level)."),
        tags=["Blog Categories"],
        responses={
            200: BlogCategorySerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    stats=extend_schema(
        summary=_("Get category statistics"),
        description=_("Get comprehensive statistics about this category."),
        tags=["Blog Categories"],
        responses={
            200: inline_serializer(
                name="BlogCategoryStatsResponse",
                fields={
                    "direct_post_count": serializers.IntegerField(),
                    "recursive_post_count": serializers.CharField(),
                    "children_count": serializers.IntegerField(),
                    "descendants_count": serializers.IntegerField(),
                    "level": serializers.IntegerField(),
                    "absolute_url": serializers.CharField(),
                    "main_image_path": serializers.CharField(),
                    "has_image": serializers.BooleanField(),
                    "is_root": serializers.BooleanField(),
                    "is_leaf": serializers.BooleanField(),
                },
            ),
            404: ErrorResponseSerializer,
        },
    ),
    tree=extend_schema(
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
        summary=_("Reorder categories"),
        description=_(
            "Batch reorder categories by updating their sort_order values."
        ),
        tags=["Blog Categories"],
        request=inline_serializer(
            name="BlogCategoryReorderRequest",
            fields={
                "categories": serializers.ListField(
                    child=inline_serializer(
                        name="BlogCategoryReorderItem",
                        fields={
                            "id": serializers.IntegerField(),
                            "sort_order": serializers.IntegerField(),
                        },
                    )
                )
            },
        ),
        responses={
            200: inline_serializer(
                name="BlogCategoryReorderResponse",
                fields={
                    "updated_count": serializers.IntegerField(),
                    "message": serializers.CharField(),
                },
            ),
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
    filterset_class = BlogCategoryFilter
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
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

    serializers = {
        "default": BlogCategorySerializer,
        "posts": BlogPostSerializer,
    }

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
        ancestors = (
            category.get_ancestors()
            .select_related("parent")
            .prefetch_related("translations")
        )
        serializer = BlogCategorySerializer(ancestors, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["GET"])
    def siblings(self, request, pk=None):
        category = self.get_object()
        queryset = (
            category.get_siblings()
            .select_related("parent")
            .prefetch_related("translations", "blog_posts")
        )
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"])
    def stats(self, request, pk=None):
        category = self.get_object()

        stats = {
            "direct_post_count": category.post_count,
            "recursive_post_count": category.recursive_post_count,
            "children_count": category.get_children().count(),
            "descendants_count": category.get_descendants().count(),
            "level": category.level,
            "absolute_url": category.absolute_url,
            "main_image_path": category.main_image_path,
            "has_image": bool(category.image),
            "is_root": category.is_root_node(),
            "is_leaf": category.is_leaf_node(),
        }

        return Response(stats)

    @action(detail=False, methods=["GET"])
    def tree(self, request):
        root_categories = (
            BlogCategory.objects.filter(parent__isnull=True)
            .select_related("parent")
            .prefetch_related(
                "translations",
                "children__translations",
                "children__children__translations",
                "blog_posts",
            )
        )

        serializer = BlogCategorySerializer(root_categories, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["POST"])
    def reorder(self, request):
        categories_data = request.data.get("categories", [])

        if not categories_data:
            return Response(
                {"detail": _("No categories provided for reordering.")},
                status=400,
            )

        updated_count = 0
        for category_data in categories_data:
            try:
                category = BlogCategory.objects.get(id=category_data["id"])
                category.sort_order = category_data["sort_order"]
                category.save(update_fields=["sort_order"])
                updated_count += 1
            except BlogCategory.DoesNotExist:
                continue

        return Response(
            {
                "updated_count": updated_count,
                "message": _("Categories reordered successfully."),
            }
        )
