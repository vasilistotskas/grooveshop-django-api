from django.db import models
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from blog.models.author import BlogAuthor


class BlogAuthorFilter(filters.FilterSet):
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
        help_text=_("Get most active authors (top 10 by post count)"),
    )
    most_liked = filters.BooleanFilter(
        method="filter_most_liked",
        help_text=_("Get most liked authors (top 10 by total likes)"),
    )

    class Meta:
        model = BlogAuthor
        fields = [
            "id",
            "user",
            "user_email",
            "first_name",
            "last_name",
            "full_name",
            "has_website",
            "website",
            "bio",
            "min_posts",
            "max_posts",
            "has_posts",
            "min_total_likes",
            "has_likes",
            "most_active",
            "most_liked",
        ]

    def filter_full_name(self, queryset, name, value):
        if not value:
            return queryset

        names = value.strip().split()
        if len(names) == 1:
            return queryset.filter(
                models.Q(user__first_name__icontains=names[0])
                | models.Q(user__last_name__icontains=names[0])
            )
        elif len(names) >= 2:
            return queryset.filter(
                user__first_name__icontains=names[0],
                user__last_name__icontains=names[-1],
            )
        return queryset

    def filter_has_website(self, queryset, name, value):
        if value is True:
            return queryset.exclude(website__isnull=True).exclude(
                website__exact=""
            )
        elif value is False:
            return queryset.filter(
                models.Q(website__isnull=True) | models.Q(website__exact="")
            )
        return queryset

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

    def filter_min_total_likes(self, queryset, name, value):
        if value is not None:
            return queryset.annotate(
                total_likes=Count("blog_posts__likes")
            ).filter(total_likes__gte=value)
        return queryset

    def filter_has_likes(self, queryset, name, value):
        if value is True:
            return queryset.annotate(
                total_likes=Count("blog_posts__likes")
            ).filter(total_likes__gt=0)
        elif value is False:
            return queryset.annotate(
                total_likes=Count("blog_posts__likes")
            ).filter(total_likes=0)
        return queryset

    def filter_most_active(self, queryset, name, value):
        if value is True:
            return (
                queryset.annotate(post_count=Count("blog_posts"))
                .filter(post_count__gt=0)
                .order_by("-post_count")[:10]
            )
        return queryset

    def filter_most_liked(self, queryset, name, value):
        if value is True:
            return (
                queryset.annotate(total_likes=Count("blog_posts__likes"))
                .filter(total_likes__gt=0)
                .order_by("-total_likes")[:10]
            )
        return queryset
