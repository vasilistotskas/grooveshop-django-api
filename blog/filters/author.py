from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from blog.models.author import BlogAuthor
from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin


class BlogAuthorFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    user = filters.NumberFilter(
        field_name="user__id",
        lookup_expr="exact",
        help_text=_("Filter by user ID"),
    )
    user_email = filters.CharFilter(
        field_name="user__email",
        lookup_expr="icontains",
        help_text=_("Filter by user email (partial match)"),
    )
    first_name = filters.CharFilter(
        field_name="user__first_name",
        lookup_expr="icontains",
        help_text=_("Filter by first name (partial match)"),
    )
    last_name = filters.CharFilter(
        field_name="user__last_name",
        lookup_expr="icontains",
        help_text=_("Filter by last name (partial match)"),
    )
    full_name = filters.CharFilter(
        method="filter_full_name",
        help_text=_("Filter by full name (first + last name)"),
    )

    has_website = filters.BooleanFilter(
        method="filter_has_website",
        help_text=_("Filter authors who have/don't have a website"),
    )
    website = filters.CharFilter(
        field_name="website",
        lookup_expr="icontains",
        help_text=_("Filter by website URL (partial match)"),
    )

    bio = filters.CharFilter(
        field_name="translations__bio",
        lookup_expr="icontains",
        help_text=_("Filter by bio content (partial match)"),
    )

    min_posts = filters.NumberFilter(
        method="filter_min_posts",
        help_text=_("Filter authors with at least X posts"),
    )
    max_posts = filters.NumberFilter(
        method="filter_max_posts",
        help_text=_("Filter authors with at most X posts"),
    )
    has_posts = filters.BooleanFilter(
        method="filter_has_posts",
        help_text=_("Filter authors who have/don't have posts"),
    )

    min_total_likes = filters.NumberFilter(
        method="filter_min_total_likes",
        help_text=_(
            "Filter authors with at least X total likes across all posts"
        ),
    )
    has_likes = filters.BooleanFilter(
        method="filter_has_likes",
        help_text=_("Filter authors who have/don't have likes on their posts"),
    )

    most_active = filters.BooleanFilter(
        method="filter_most_active",
        help_text=_("Get authors ordered by post count (most active first)"),
    )
    most_liked = filters.BooleanFilter(
        method="filter_most_liked",
        help_text=_("Get authors ordered by total likes (most liked first)"),
    )

    class Meta:
        model = BlogAuthor
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "website": ["exact", "icontains"],
            "user": ["exact"],
            "user__email": ["exact", "icontains"],
            "user__first_name": ["exact", "icontains"],
            "user__last_name": ["exact", "icontains"],
        }

    def filter_full_name(self, queryset, name, value):
        """Filter by full name (supports first name, last name, or both)"""
        if not value:
            return queryset

        names = value.strip().split()
        if len(names) == 1:
            return queryset.filter(
                Q(user__first_name__icontains=names[0])
                | Q(user__last_name__icontains=names[0])
            )
        elif len(names) >= 2:
            return queryset.filter(
                user__first_name__icontains=names[0],
                user__last_name__icontains=names[-1],
            )
        return queryset

    def filter_has_website(self, queryset, name, value):
        """Filter authors who have/don't have a website"""
        if value is True:
            return queryset.exclude(
                Q(website__isnull=True) | Q(website__exact="")
            )
        elif value is False:
            return queryset.filter(
                Q(website__isnull=True) | Q(website__exact="")
            )
        return queryset

    def filter_min_posts(self, queryset, name, value):
        """Filter authors with minimum number of posts"""
        if value is not None and value >= 0:
            return queryset.annotate(
                post_count=Count("blog_posts", distinct=True)
            ).filter(post_count__gte=value)
        return queryset

    def filter_max_posts(self, queryset, name, value):
        """Filter authors with maximum number of posts"""
        if value is not None and value >= 0:
            return queryset.annotate(
                post_count=Count("blog_posts", distinct=True)
            ).filter(post_count__lte=value)
        return queryset

    def filter_has_posts(self, queryset, name, value):
        """Filter authors who have/don't have posts"""
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
        """Filter authors with minimum total likes across all posts"""
        if value is not None and value >= 0:
            return queryset.annotate(
                total_likes=Count("blog_posts__likes", distinct=True)
            ).filter(total_likes__gte=value)
        return queryset

    def filter_has_likes(self, queryset, name, value):
        """Filter authors who have/don't have likes on their posts"""
        if value is True:
            return queryset.annotate(
                total_likes=Count("blog_posts__likes", distinct=True)
            ).filter(total_likes__gt=0)
        elif value is False:
            return queryset.annotate(
                total_likes=Count("blog_posts__likes", distinct=True)
            ).filter(total_likes=0)
        return queryset

    def filter_most_active(self, queryset, name, value):
        """Order authors by post count (most active first)"""
        if value is True:
            return (
                queryset.annotate(post_count=Count("blog_posts", distinct=True))
                .filter(post_count__gt=0)
                .order_by("-post_count", "-created_at")
            )
        return queryset

    def filter_most_liked(self, queryset, name, value):
        """Order authors by total likes (most liked first)"""
        if value is True:
            return (
                queryset.annotate(
                    total_likes=Count("blog_posts__likes", distinct=True)
                )
                .filter(total_likes__gt=0)
                .order_by("-total_likes", "-created_at")
            )
        return queryset
