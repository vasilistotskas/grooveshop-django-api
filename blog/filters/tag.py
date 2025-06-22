from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from blog.models.tag import BlogTag


class BlogTagFilter(filters.FilterSet):
    id = filters.NumberFilter(
        field_name="id",
        lookup_expr="exact",
        help_text=_("Filter by tag ID"),
    )
    active = filters.BooleanFilter(
        field_name="active",
        help_text=_("Filter by active status"),
    )
    name = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by tag name (partial match)"),
    )

    min_posts = filters.NumberFilter(
        method="filter_min_posts",
        help_text=_("Filter tags with at least X posts"),
    )
    max_posts = filters.NumberFilter(
        method="filter_max_posts",
        help_text=_("Filter tags with at most X posts"),
    )
    has_posts = filters.BooleanFilter(
        method="filter_has_posts",
        help_text=_("Filter tags that have/don't have posts"),
    )

    class Meta:
        model = BlogTag
        fields = [
            "id",
            "active",
            "name",
            "min_posts",
            "max_posts",
            "has_posts",
        ]

    def filter_min_posts(self, queryset, name, value):
        if value is not None:
            return queryset.annotate(post_count=Count("blog_posts")).filter(
                post_count__gte=value
            )
        return queryset

    def filter_max_posts(self, queryset, name, value):
        if value is not None:
            return queryset.annotate(post_count=Count("blog_posts")).filter(
                post_count__lte=value
            )
        return queryset

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
