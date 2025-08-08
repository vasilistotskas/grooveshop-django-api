from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from blog.models import BlogPost
from core.filters.camel_case_filters import (
    CamelCasePublishableTimeStampFilterSet,
)
from core.filters.core import UUIDFilterMixin


class BlogPostFilter(
    UUIDFilterMixin,
    CamelCasePublishableTimeStampFilterSet,
):
    title = filters.CharFilter(
        field_name="translations__title",
        lookup_expr="icontains",
        help_text=_("Filter by title (case-insensitive)"),
    )
    author_email = filters.CharFilter(
        field_name="author__user__email",
        lookup_expr="icontains",
        help_text=_("Filter by author email (case-insensitive)"),
    )
    min_likes = filters.NumberFilter(
        field_name="likes_count_field",
        lookup_expr="gte",
        label="Minimum Likes Count",
        help_text=_("Filter by minimum number of likes"),
    )
    min_comments = filters.NumberFilter(
        field_name="comments_count_field",
        lookup_expr="gte",
        label="Minimum Comments Count",
        help_text=_("Filter by minimum number of comments"),
    )
    min_tags = filters.NumberFilter(
        field_name="tags_count_field",
        lookup_expr="gte",
        label="Minimum Tags Count",
        help_text=_("Filter by minimum number of tags"),
    )
    featured = filters.BooleanFilter(
        field_name="featured",
        lookup_expr="exact",
        help_text=_("Filter by featured status"),
    )
    min_view_count = filters.NumberFilter(
        field_name="view_count",
        lookup_expr="gte",
        label="Minimum View Count",
        help_text=_("Filter by minimum number of views"),
    )
    category_name = filters.CharFilter(
        field_name="category__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by category name (case-insensitive)"),
    )
    tag_name = filters.CharFilter(
        field_name="tags__translations__label",
        lookup_expr="icontains",
        help_text=_("Filter by tag label (case-insensitive)"),
    )
    author_name = filters.CharFilter(
        method="filter_author_name",
        help_text=_("Filter by author full name (case-insensitive)"),
    )

    category = filters.ModelChoiceFilter(
        field_name="category",
        queryset=None,
        help_text=_("Filter by category ID"),
    )
    author = filters.ModelChoiceFilter(
        field_name="author",
        queryset=None,
        help_text=_("Filter by author ID"),
    )
    tags = filters.ModelMultipleChoiceFilter(
        field_name="tags",
        queryset=None,
        help_text=_("Filter by tag IDs (comma-separated)"),
    )

    class Meta:
        model = BlogPost
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "published_at": ["gte", "lte", "date"],
            "is_published": ["exact"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "slug": ["exact", "icontains"],
            "featured": ["exact"],
            "view_count": ["gte", "lte", "exact"],
            "category": ["exact"],
            "author": ["exact"],
            "tags": ["exact"],
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from blog.models import BlogCategory, BlogAuthor, BlogTag

        self.filters["category"].queryset = BlogCategory.objects.all()
        self.filters["author"].queryset = BlogAuthor.objects.all()
        self.filters["tags"].queryset = BlogTag.objects.all()

    def filter_author_name(self, queryset, name, value):
        if value:
            return queryset.filter(
                Q(author__user__first_name__icontains=value)
                | Q(author__user__last_name__icontains=value)
                | Q(author__user__username__icontains=value)
            )
        return queryset
