import django_filters
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from blog.models.comment import BlogComment


class BlogCommentFilter(django_filters.FilterSet):
    is_approved = django_filters.BooleanFilter(
        help_text=_("Filter by approval status")
    )
    has_content = django_filters.BooleanFilter(
        method="filter_has_content",
        help_text=_(
            "Filter comments that have content (true) or are empty (false)"
        ),
    )
    content = django_filters.CharFilter(
        field_name="translations__content",
        lookup_expr="icontains",
        help_text=_("Filter by comment content (case-insensitive)"),
    )

    post = django_filters.NumberFilter(
        field_name="post__id", help_text=_("Filter by blog post ID")
    )
    user = django_filters.NumberFilter(
        field_name="user__id", help_text=_("Filter by user ID")
    )
    post__slug = django_filters.CharFilter(
        field_name="post__slug", help_text=_("Filter by blog post slug")
    )

    parent = django_filters.NumberFilter(
        field_name="parent__id", help_text=_("Filter by parent comment ID")
    )
    parent__isnull = django_filters.BooleanFilter(
        field_name="parent",
        lookup_expr="isnull",
        help_text=_("Filter top-level comments (true) or replies (false)"),
    )
    level = django_filters.NumberFilter(
        help_text=_("Filter by comment nesting level (0 for top-level)")
    )
    level__lte = django_filters.NumberFilter(
        field_name="level",
        lookup_expr="lte",
        help_text=_("Filter comments at or above this nesting level"),
    )
    level__gte = django_filters.NumberFilter(
        field_name="level",
        lookup_expr="gte",
        help_text=_("Filter comments at or below this nesting level"),
    )

    has_likes = django_filters.BooleanFilter(
        method="filter_has_likes",
        help_text=_(
            "Filter comments that have likes (true) or no likes (false)"
        ),
    )
    min_likes = django_filters.NumberFilter(
        method="filter_min_likes",
        help_text=_("Filter comments with at least this many likes"),
    )
    has_replies = django_filters.BooleanFilter(
        method="filter_has_replies",
        help_text=_(
            "Filter comments that have replies (true) or no replies (false)"
        ),
    )
    min_replies = django_filters.NumberFilter(
        method="filter_min_replies",
        help_text=_("Filter comments with at least this many replies"),
    )

    user__is_staff = django_filters.BooleanFilter(
        field_name="user__is_staff",
        help_text=_("Filter comments by staff users"),
    )
    user__is_active = django_filters.BooleanFilter(
        field_name="user__is_active",
        help_text=_("Filter comments by active users"),
    )

    post__category = django_filters.NumberFilter(
        field_name="post__category__id",
        help_text=_("Filter by blog post category ID"),
    )
    post__category__slug = django_filters.CharFilter(
        field_name="post__category__slug",
        help_text=_("Filter by blog post category slug"),
    )

    class Meta:
        model = BlogComment
        fields = {
            "id": ["exact", "in"],
            "tree_id": ["exact"],
            "lft": ["gte", "lte"],
            "rght": ["gte", "lte"],
        }

    def filter_has_content(self, queryset, name, value):
        if value is True:
            return queryset.exclude(
                Q(translations__content__isnull=True)
                | Q(translations__content__exact="")
            )
        elif value is False:
            return queryset.filter(
                Q(translations__content__isnull=True)
                | Q(translations__content__exact="")
            )
        return queryset

    def filter_has_likes(self, queryset, name, value):
        if value is True:
            return queryset.annotate(like_count=Count("likes")).filter(
                like_count__gt=0
            )
        elif value is False:
            return queryset.annotate(like_count=Count("likes")).filter(
                like_count=0
            )
        return queryset

    def filter_min_likes(self, queryset, name, value):
        return queryset.annotate(like_count=Count("likes")).filter(
            like_count__gte=value
        )

    def filter_has_replies(self, queryset, name, value):
        if value is True:
            return queryset.annotate(reply_count=Count("children")).filter(
                reply_count__gt=0
            )
        elif value is False:
            return queryset.annotate(reply_count=Count("children")).filter(
                reply_count=0
            )
        return queryset

    def filter_min_replies(self, queryset, name, value):
        return queryset.annotate(reply_count=Count("children")).filter(
            reply_count__gte=value
        )
