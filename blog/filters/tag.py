from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from blog.models.tag import BlogTag
from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin, SortableFilterMixin


class BlogTagFilter(
    UUIDFilterMixin, SortableFilterMixin, CamelCaseTimeStampFilterSet
):
    active = filters.BooleanFilter(
        field_name="active",
        help_text=_("Filter by active status"),
    )

    name = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by tag name (partial match)"),
    )
    name__exact = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="exact",
        help_text=_("Filter by exact tag name"),
    )
    name__startswith = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="istartswith",
        help_text=_("Filter tags with names starting with"),
    )
    has_name = filters.BooleanFilter(
        method="filter_has_name",
        help_text=_("Filter tags that have/don't have a name"),
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

    post = filters.NumberFilter(
        field_name="blog_posts__id",
        help_text=_("Filter tags used by specific post ID"),
    )
    post__author = filters.NumberFilter(
        field_name="blog_posts__author__id",
        help_text=_("Filter tags used in posts by specific author"),
    )
    post__category = filters.NumberFilter(
        field_name="blog_posts__category__id",
        help_text=_("Filter tags used in posts from specific category"),
    )
    post__is_published = filters.BooleanFilter(
        field_name="blog_posts__is_published",
        help_text=_("Filter tags used in published/unpublished posts"),
    )

    min_total_likes = filters.NumberFilter(
        method="filter_min_total_likes",
        help_text=_("Filter tags with posts having at least X total likes"),
    )
    has_liked_posts = filters.BooleanFilter(
        method="filter_has_liked_posts",
        help_text=_("Filter tags used in posts that have/don't have likes"),
    )

    most_used = filters.BooleanFilter(
        method="filter_most_used",
        help_text=_("Order tags by usage count (most used first)"),
    )
    most_liked = filters.BooleanFilter(
        method="filter_most_liked",
        help_text=_("Order tags by total likes on posts using them"),
    )
    unused = filters.BooleanFilter(
        method="filter_unused",
        help_text=_("Filter tags not used in any posts"),
    )

    class Meta:
        model = BlogTag
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "sort_order": ["exact", "gte", "lte"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "active": ["exact"],
            "translations__name": ["exact", "icontains", "istartswith"],
        }

    def filter_has_name(self, queryset, name, value):
        """Filter tags based on whether they have a name."""
        if value is True:
            return queryset.exclude(
                Q(translations__name__isnull=True)
                | Q(translations__name__exact="")
            )
        elif value is False:
            return queryset.filter(
                Q(translations__name__isnull=True)
                | Q(translations__name__exact="")
            )
        return queryset

    def filter_min_posts(self, queryset, name, value):
        """Filter tags with minimum number of posts."""
        if value is not None:
            return queryset.annotate(
                post_count=Count("blog_posts", distinct=True)
            ).filter(post_count__gte=value)
        return queryset

    def filter_max_posts(self, queryset, name, value):
        """Filter tags with maximum number of posts."""
        if value is not None:
            return queryset.annotate(
                post_count=Count("blog_posts", distinct=True)
            ).filter(post_count__lte=value)
        return queryset

    def filter_has_posts(self, queryset, name, value):
        """Filter tags based on whether they have posts."""
        if value is True:
            return queryset.annotate(
                post_count=Count("blog_posts", distinct=True)
            ).filter(post_count__gt=0)
        elif value is False:
            return queryset.annotate(
                post_count=Count("blog_posts", distinct=True)
            ).filter(post_count=0)
        return queryset

    def filter_min_total_likes(self, queryset, name, value):
        """Filter tags with minimum total likes across all posts."""
        if value is not None:
            return queryset.annotate(
                total_likes=Count("blog_posts__likes", distinct=True)
            ).filter(total_likes__gte=value)
        return queryset

    def filter_has_liked_posts(self, queryset, name, value):
        """Filter tags based on whether they're used in posts with likes."""
        if value is True:
            return queryset.annotate(
                total_likes=Count("blog_posts__likes", distinct=True)
            ).filter(total_likes__gt=0)
        elif value is False:
            return queryset.annotate(
                total_likes=Count("blog_posts__likes", distinct=True)
            ).filter(total_likes=0)
        return queryset

    def filter_most_used(self, queryset, name, value):
        """Order tags by usage count."""
        if value is True:
            return queryset.annotate(
                post_count=Count("blog_posts", distinct=True)
            ).order_by("-post_count", "sort_order")
        return queryset

    def filter_most_liked(self, queryset, name, value):
        """Order tags by total likes on posts using them."""
        if value is True:
            return queryset.annotate(
                total_likes=Count("blog_posts__likes", distinct=True)
            ).order_by("-total_likes", "sort_order")
        return queryset

    def filter_unused(self, queryset, name, value):
        """Filter tags not used in any posts."""
        if value is True:
            return queryset.annotate(
                post_count=Count("blog_posts", distinct=True)
            ).filter(post_count=0)
        return queryset
