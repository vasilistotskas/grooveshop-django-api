from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from blog.models.tag import BlogTag
from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import SortableFilterMixin, UUIDFilterMixin


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

    # The counting filters read ``BlogTagQuerySet.with_engagement``,
    # which the list queryset already carries; ``_engaged`` adds it
    # otherwise.

    @staticmethod
    def _engaged(queryset):
        if "posts_count" in queryset.query.annotations:
            return queryset
        return queryset.with_engagement()

    def filter_min_posts(self, queryset, name, value):
        """Filter tags with at least X posts."""
        if value is None:
            return queryset
        return self._engaged(queryset).filter(posts_count__gte=value)

    def filter_max_posts(self, queryset, name, value):
        """Filter tags with at most X posts."""
        if value is None:
            return queryset
        return self._engaged(queryset).filter(posts_count__lte=value)

    def filter_has_posts(self, queryset, name, value):
        """Filter tags that have/don't have posts."""
        if value is None:
            return queryset
        engaged = self._engaged(queryset)
        return (
            engaged.filter(posts_count__gt=0)
            if value
            else engaged.filter(posts_count=0)
        )

    def filter_min_total_likes(self, queryset, name, value):
        """Filter tags whose posts have at least X likes in total."""
        if value is None:
            return queryset
        return self._engaged(queryset).filter(total_likes__gte=value)

    def filter_has_liked_posts(self, queryset, name, value):
        """Filter tags used in posts that have/don't have likes."""
        if value is None:
            return queryset
        engaged = self._engaged(queryset)
        return (
            engaged.filter(total_likes__gt=0)
            if value
            else engaged.filter(total_likes=0)
        )

    def filter_unused(self, queryset, name, value):
        """Filter tags not used in any posts."""
        if value is not True:
            return queryset
        return self._engaged(queryset).filter(posts_count=0)
