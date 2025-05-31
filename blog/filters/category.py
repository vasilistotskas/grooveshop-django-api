import django_filters
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from blog.models.category import BlogCategory


class BlogCategoryFilter(django_filters.FilterSet):
    parent = django_filters.NumberFilter(
        field_name="parent__id", help_text=_("Filter by parent category ID")
    )
    parent__isnull = django_filters.BooleanFilter(
        field_name="parent",
        lookup_expr="isnull",
        help_text=_("Filter root categories (true) or non-root (false)"),
    )
    level = django_filters.NumberFilter(
        help_text=_("Filter by tree level (0 for root categories)")
    )
    level__gte = django_filters.NumberFilter(
        field_name="level",
        lookup_expr="gte",
        help_text=_("Filter categories at or below this level"),
    )
    level__lte = django_filters.NumberFilter(
        field_name="level",
        lookup_expr="lte",
        help_text=_("Filter categories at or above this level"),
    )

    has_posts = django_filters.BooleanFilter(
        method="filter_has_posts",
        help_text=_(
            "Filter categories that have posts (true) or no posts (false)"
        ),
    )
    min_post_count = django_filters.NumberFilter(
        method="filter_min_post_count",
        help_text=_("Filter categories with at least this many posts"),
    )

    name = django_filters.CharFilter(
        field_name="translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by category name (case-insensitive)"),
    )
    description = django_filters.CharFilter(
        field_name="translations__description",
        lookup_expr="icontains",
        help_text=_("Filter by description content (case-insensitive)"),
    )

    class Meta:
        model = BlogCategory
        fields = {
            "id": ["exact", "in"],
            "slug": ["exact", "icontains"],
            "sort_order": ["exact", "gte", "lte"],
        }

    def filter_has_posts(self, queryset, name, value):
        if value is True:
            return queryset.annotate(post_count=Count("blog_posts")).filter(
                post_count__gt=0
            )
        elif value is False:
            return queryset.annotate(post_count=Count("blog_posts")).filter(
                post_count=0
            )
        return queryset

    def filter_min_post_count(self, queryset, name, value):
        return queryset.annotate(post_count=Count("blog_posts")).filter(
            post_count__gte=value
        )
