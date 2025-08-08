from django.db.models import Count, Q, F
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from blog.models.category import BlogCategory
from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin, SortableFilterMixin


class BlogCategoryFilter(
    UUIDFilterMixin, SortableFilterMixin, CamelCaseTimeStampFilterSet
):
    parent = filters.NumberFilter(
        field_name="parent__id", help_text=_("Filter by parent category ID")
    )
    parent__isnull = filters.BooleanFilter(
        field_name="parent",
        lookup_expr="isnull",
        help_text=_("Filter root categories (true) or non-root (false)"),
    )

    level = filters.NumberFilter(
        help_text=_("Filter by tree level (0 for root categories)")
    )
    level__gte = filters.NumberFilter(
        field_name="level",
        lookup_expr="gte",
        help_text=_("Filter categories at or below this level"),
    )
    level__lte = filters.NumberFilter(
        field_name="level",
        lookup_expr="lte",
        help_text=_("Filter categories at or above this level"),
    )

    lft = filters.NumberFilter(
        field_name="lft",
        help_text=_("Filter by left tree value (MPTT internal)"),
    )
    rght = filters.NumberFilter(
        field_name="rght",
        help_text=_("Filter by right tree value (MPTT internal)"),
    )
    tree_id = filters.NumberFilter(
        field_name="tree_id", help_text=_("Filter by tree ID (MPTT internal)")
    )

    name = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by category name (case-insensitive)"),
    )
    description = filters.CharFilter(
        field_name="translations__description",
        lookup_expr="icontains",
        help_text=_("Filter by description content (case-insensitive)"),
    )

    has_image = filters.BooleanFilter(
        method="filter_has_image",
        help_text=_("Filter categories that have/don't have an image"),
    )

    has_posts = filters.BooleanFilter(
        method="filter_has_posts",
        help_text=_(
            "Filter categories that have posts (true) or no posts (false)"
        ),
    )
    min_post_count = filters.NumberFilter(
        method="filter_min_post_count",
        help_text=_("Filter categories with at least this many posts"),
    )
    max_post_count = filters.NumberFilter(
        method="filter_max_post_count",
        help_text=_("Filter categories with at most this many posts"),
    )
    has_recursive_posts = filters.BooleanFilter(
        method="filter_has_recursive_posts",
        help_text=_(
            "Filter categories that have posts in themselves or descendants"
        ),
    )
    min_recursive_post_count = filters.NumberFilter(
        method="filter_min_recursive_post_count",
        help_text=_(
            "Filter categories with at least this many posts (including descendants)"
        ),
    )

    is_leaf = filters.BooleanFilter(
        method="filter_is_leaf",
        help_text=_("Filter leaf categories (no children)"),
    )
    has_children = filters.BooleanFilter(
        method="filter_has_children",
        help_text=_("Filter categories that have/don't have children"),
    )
    ancestor_of = filters.NumberFilter(
        method="filter_ancestor_of",
        help_text=_(
            "Filter categories that are ancestors of the given category ID"
        ),
    )
    descendant_of = filters.NumberFilter(
        method="filter_descendant_of",
        help_text=_(
            "Filter categories that are descendants of the given category ID"
        ),
    )

    class Meta:
        model = BlogCategory
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "sort_order": ["exact", "gte", "lte"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "slug": ["exact", "icontains"],
            "level": ["exact", "gte", "lte"],
            "lft": ["exact", "gte", "lte"],
            "rght": ["exact", "gte", "lte"],
            "tree_id": ["exact"],
            "parent": ["exact", "isnull"],
        }

    def filter_has_image(self, queryset, name, value):
        """Filter categories based on whether they have an image."""
        if value is True:
            return queryset.exclude(Q(image__isnull=True) | Q(image__exact=""))
        elif value is False:
            return queryset.filter(Q(image__isnull=True) | Q(image__exact=""))
        return queryset

    def filter_has_posts(self, queryset, name, value):
        """Filter categories based on direct post count."""
        if value is True:
            return queryset.annotate(
                direct_post_count=Count("blog_posts")
            ).filter(direct_post_count__gt=0)
        elif value is False:
            return queryset.annotate(
                direct_post_count=Count("blog_posts")
            ).filter(direct_post_count=0)
        return queryset

    def filter_min_post_count(self, queryset, name, value):
        """Filter categories with minimum direct post count."""
        if value is not None:
            return queryset.annotate(
                direct_post_count=Count("blog_posts")
            ).filter(direct_post_count__gte=value)
        return queryset

    def filter_max_post_count(self, queryset, name, value):
        """Filter categories with maximum direct post count."""
        if value is not None:
            return queryset.annotate(
                direct_post_count=Count("blog_posts")
            ).filter(direct_post_count__lte=value)
        return queryset

    def filter_has_recursive_posts(self, queryset, name, value):
        """Filter categories based on recursive post count (including descendants)."""
        result_ids = []
        for category in queryset:
            descendants = category.get_descendants(include_self=True)
            post_count = category.__class__.objects.filter(
                id__in=descendants.values_list("id", flat=True)
            ).aggregate(total_posts=Count("blog_posts", distinct=True))[
                "total_posts"
            ]

            if value is True and post_count > 0:
                result_ids.append(category.id)
            elif value is False and post_count == 0:
                result_ids.append(category.id)

        return queryset.filter(id__in=result_ids)

    def filter_min_recursive_post_count(self, queryset, name, value):
        """Filter categories with minimum recursive post count."""
        if value is not None:
            result_ids = []
            for category in queryset:
                descendants = category.get_descendants(include_self=True)
                post_count = category.__class__.objects.filter(
                    id__in=descendants.values_list("id", flat=True)
                ).aggregate(total_posts=Count("blog_posts", distinct=True))[
                    "total_posts"
                ]

                if post_count >= value:
                    result_ids.append(category.id)

            return queryset.filter(id__in=result_ids)
        return queryset

    def filter_is_leaf(self, queryset, name, value):
        """Filter leaf nodes (categories without children)."""
        if value is True:
            return queryset.filter(lft=F("rght") - 1)
        elif value is False:
            return queryset.exclude(lft=F("rght") - 1)
        return queryset

    def filter_has_children(self, queryset, name, value):
        """Filter categories based on whether they have children."""
        if value is True:
            return queryset.exclude(lft=F("rght") - 1)
        elif value is False:
            return queryset.filter(lft=F("rght") - 1)
        return queryset

    def filter_ancestor_of(self, queryset, name, value):
        """Filter categories that are ancestors of the given category."""
        try:
            category = BlogCategory.objects.get(id=value)
            ancestor_ids = category.get_ancestors().values_list("id", flat=True)
            return queryset.filter(id__in=ancestor_ids)
        except BlogCategory.DoesNotExist:
            return queryset.none()

    def filter_descendant_of(self, queryset, name, value):
        """Filter categories that are descendants of the given category."""
        try:
            category = BlogCategory.objects.get(id=value)
            descendant_ids = category.get_descendants().values_list(
                "id", flat=True
            )
            return queryset.filter(id__in=descendant_ids)
        except BlogCategory.DoesNotExist:
            return queryset.none()
