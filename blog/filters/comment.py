from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from blog.models.comment import BlogComment
from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin


class BlogCommentFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    approved = filters.BooleanFilter(help_text=_("Filter by approval status"))

    has_content = filters.BooleanFilter(
        method="filter_has_content",
        help_text=_(
            "Filter comments that have content (true) or are empty (false)"
        ),
    )
    content = filters.CharFilter(
        field_name="translations__content",
        lookup_expr="icontains",
        help_text=_("Filter by comment content (case-insensitive)"),
    )
    content_length = filters.NumberFilter(
        method="filter_content_length",
        help_text=_("Filter by exact content length"),
    )
    min_content_length = filters.NumberFilter(
        method="filter_min_content_length",
        help_text=_("Filter comments with at least this content length"),
    )
    max_content_length = filters.NumberFilter(
        method="filter_max_content_length",
        help_text=_("Filter comments with at most this content length"),
    )

    post = filters.NumberFilter(
        field_name="post__id", help_text=_("Filter by blog post ID")
    )
    post__slug = filters.CharFilter(
        field_name="post__slug", help_text=_("Filter by blog post slug")
    )
    post__title = filters.CharFilter(
        field_name="post__translations__title",
        lookup_expr="icontains",
        help_text=_("Filter by blog post title"),
    )
    post__is_published = filters.BooleanFilter(
        field_name="post__is_published",
        help_text=_("Filter by blog post published status"),
    )
    post__category = filters.NumberFilter(
        field_name="post__category__id",
        help_text=_("Filter by blog post category ID"),
    )
    post__category__slug = filters.CharFilter(
        field_name="post__category__slug",
        help_text=_("Filter by blog post category slug"),
    )
    post__author = filters.NumberFilter(
        field_name="post__author__id",
        help_text=_("Filter by blog post author ID"),
    )

    user = filters.NumberFilter(
        field_name="user__id", help_text=_("Filter by user ID")
    )
    user__email = filters.CharFilter(
        field_name="user__email",
        lookup_expr="icontains",
        help_text=_("Filter by user email"),
    )
    user__is_staff = filters.BooleanFilter(
        field_name="user__is_staff",
        help_text=_("Filter comments by staff users"),
    )
    user__is_active = filters.BooleanFilter(
        field_name="user__is_active",
        help_text=_("Filter comments by active users"),
    )
    is_anonymous = filters.BooleanFilter(
        method="filter_is_anonymous",
        help_text=_("Filter anonymous comments (no user)"),
    )

    parent = filters.NumberFilter(
        field_name="parent__id", help_text=_("Filter by parent comment ID")
    )
    parent__isnull = filters.BooleanFilter(
        field_name="parent",
        lookup_expr="isnull",
        help_text=_("Filter top-level comments (true) or replies (false)"),
    )

    level = filters.NumberFilter(
        help_text=_("Filter by comment nesting level (0 for top-level)")
    )
    level__lte = filters.NumberFilter(
        field_name="level",
        lookup_expr="lte",
        help_text=_("Filter comments at or above this nesting level"),
    )
    level__gte = filters.NumberFilter(
        field_name="level",
        lookup_expr="gte",
        help_text=_("Filter comments at or below this nesting level"),
    )

    tree_id = filters.NumberFilter(
        help_text=_("Filter by tree ID (MPTT internal)")
    )
    lft = filters.NumberFilter(
        field_name="lft",
        help_text=_("Filter by left tree value (MPTT internal)"),
    )
    rght = filters.NumberFilter(
        field_name="rght",
        help_text=_("Filter by right tree value (MPTT internal)"),
    )

    has_likes = filters.BooleanFilter(
        method="filter_has_likes",
        help_text=_(
            "Filter comments that have likes (true) or no likes (false)"
        ),
    )
    min_likes = filters.NumberFilter(
        method="filter_min_likes",
        help_text=_("Filter comments with at least this many likes"),
    )
    max_likes = filters.NumberFilter(
        method="filter_max_likes",
        help_text=_("Filter comments with at most this many likes"),
    )
    liked_by = filters.NumberFilter(
        method="filter_liked_by",
        help_text=_("Filter comments liked by specific user ID"),
    )

    has_replies = filters.BooleanFilter(
        method="filter_has_replies",
        help_text=_(
            "Filter comments that have approved replies (true) or no replies (false)"
        ),
    )
    min_replies = filters.NumberFilter(
        method="filter_min_replies",
        help_text=_("Filter comments with at least this many approved replies"),
    )
    max_replies = filters.NumberFilter(
        method="filter_max_replies",
        help_text=_("Filter comments with at most this many approved replies"),
    )

    is_leaf = filters.BooleanFilter(
        method="filter_is_leaf",
        help_text=_("Filter leaf comments (no approved replies)"),
    )
    ancestor_of = filters.NumberFilter(
        method="filter_ancestor_of",
        help_text=_(
            "Filter comments that are ancestors of the given comment ID"
        ),
    )
    descendant_of = filters.NumberFilter(
        method="filter_descendant_of",
        help_text=_(
            "Filter comments that are descendants of the given comment ID"
        ),
    )

    most_liked = filters.BooleanFilter(
        method="filter_most_liked",
        help_text=_("Order comments by most likes first"),
    )
    most_replied = filters.BooleanFilter(
        method="filter_most_replied",
        help_text=_("Order comments by most approved replies first"),
    )

    class Meta:
        model = BlogComment
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "approved": ["exact"],
            "tree_id": ["exact"],
            "lft": ["gte", "lte"],
            "rght": ["gte", "lte"],
            "level": ["exact", "gte", "lte"],
            "post": ["exact"],
            "user": ["exact"],
            "parent": ["exact", "isnull"],
        }

    def filter_has_content(self, queryset, name, value):
        """Filter comments based on whether they have content."""
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

    def filter_content_length(self, queryset, name, value):
        """Filter by exact content length."""
        if value is not None:
            from django.db.models.functions import Length

            return queryset.annotate(
                content_len=Length("translations__content")
            ).filter(content_len=value)
        return queryset

    def filter_min_content_length(self, queryset, name, value):
        """Filter by minimum content length."""
        if value is not None:
            from django.db.models.functions import Length

            return queryset.annotate(
                content_len=Length("translations__content")
            ).filter(content_len__gte=value)
        return queryset

    def filter_max_content_length(self, queryset, name, value):
        """Filter by maximum content length."""
        if value is not None:
            from django.db.models.functions import Length

            return queryset.annotate(
                content_len=Length("translations__content")
            ).filter(content_len__lte=value)
        return queryset

    def filter_is_anonymous(self, queryset, name, value):
        """Filter anonymous comments (no user)."""
        if value is True:
            return queryset.filter(user__isnull=True)
        elif value is False:
            return queryset.filter(user__isnull=False)
        return queryset

    def filter_has_likes(self, queryset, name, value):
        """Filter comments based on whether they have likes."""
        if value is True:
            return queryset.annotate(
                like_count=Count("likes", distinct=True)
            ).filter(like_count__gt=0)
        elif value is False:
            return queryset.annotate(
                like_count=Count("likes", distinct=True)
            ).filter(like_count=0)
        return queryset

    def filter_min_likes(self, queryset, name, value):
        """Filter comments with minimum number of likes."""
        if value is not None:
            return queryset.annotate(
                like_count=Count("likes", distinct=True)
            ).filter(like_count__gte=value)
        return queryset

    def filter_max_likes(self, queryset, name, value):
        """Filter comments with maximum number of likes."""
        if value is not None:
            return queryset.annotate(
                like_count=Count("likes", distinct=True)
            ).filter(like_count__lte=value)
        return queryset

    def filter_liked_by(self, queryset, name, value):
        """Filter comments liked by specific user."""
        if value is not None:
            return queryset.filter(likes__id=value)
        return queryset

    def filter_has_replies(self, queryset, name, value):
        """Filter comments based on whether they have approved replies."""
        if value is True:
            return queryset.annotate(
                approved_reply_count=Count(
                    "children", filter=Q(children__approved=True), distinct=True
                )
            ).filter(approved_reply_count__gt=0)
        elif value is False:
            return queryset.annotate(
                approved_reply_count=Count(
                    "children", filter=Q(children__approved=True), distinct=True
                )
            ).filter(approved_reply_count=0)
        return queryset

    def filter_min_replies(self, queryset, name, value):
        """Filter comments with minimum number of approved replies."""
        if value is not None:
            return queryset.annotate(
                approved_reply_count=Count(
                    "children", filter=Q(children__approved=True), distinct=True
                )
            ).filter(approved_reply_count__gte=value)
        return queryset

    def filter_max_replies(self, queryset, name, value):
        """Filter comments with maximum number of approved replies."""
        if value is not None:
            return queryset.annotate(
                approved_reply_count=Count(
                    "children", filter=Q(children__approved=True), distinct=True
                )
            ).filter(approved_reply_count__lte=value)
        return queryset

    def filter_is_leaf(self, queryset, name, value):
        """Filter leaf nodes (comments without approved replies)."""
        if value is True:
            return queryset.annotate(
                approved_reply_count=Count(
                    "children", filter=Q(children__approved=True), distinct=True
                )
            ).filter(approved_reply_count=0)
        elif value is False:
            return queryset.annotate(
                approved_reply_count=Count(
                    "children", filter=Q(children__approved=True), distinct=True
                )
            ).filter(approved_reply_count__gt=0)
        return queryset

    def filter_ancestor_of(self, queryset, name, value):
        """Filter comments that are ancestors of the given comment."""
        try:
            comment = BlogComment.objects.get(id=value)
            ancestor_ids = comment.get_ancestors().values_list("id", flat=True)
            return queryset.filter(id__in=ancestor_ids)
        except BlogComment.DoesNotExist:
            return queryset.none()

    def filter_descendant_of(self, queryset, name, value):
        """Filter comments that are descendants of the given comment."""
        try:
            comment = BlogComment.objects.get(id=value)
            descendant_ids = comment.get_descendants().values_list(
                "id", flat=True
            )
            return queryset.filter(id__in=descendant_ids)
        except BlogComment.DoesNotExist:
            return queryset.none()

    def filter_most_liked(self, queryset, name, value):
        """Order comments by most likes."""
        if value is True:
            return queryset.annotate(
                like_count=Count("likes", distinct=True)
            ).order_by("-like_count", "-created_at")
        return queryset

    def filter_most_replied(self, queryset, name, value):
        """Order comments by most approved replies."""
        if value is True:
            return queryset.annotate(
                approved_reply_count=Count(
                    "children", filter=Q(children__approved=True), distinct=True
                )
            ).order_by("-approved_reply_count", "-created_at")
        return queryset
