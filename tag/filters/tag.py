from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from tag.models.tag import Tag
from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin, SortableFilterMixin


class TagFilter(
    UUIDFilterMixin, SortableFilterMixin, CamelCaseTimeStampFilterSet
):
    active = filters.BooleanFilter(
        field_name="active",
        help_text=_("Filter by active status"),
    )

    label = filters.CharFilter(
        field_name="translations__label",
        lookup_expr="icontains",
        help_text=_("Filter by tag label (partial match)"),
    )
    label__exact = filters.CharFilter(
        field_name="translations__label",
        lookup_expr="exact",
        help_text=_("Filter by exact tag label"),
    )
    label__startswith = filters.CharFilter(
        field_name="translations__label",
        lookup_expr="istartswith",
        help_text=_("Filter tags with labels starting with"),
    )
    has_label = filters.BooleanFilter(
        method="filter_has_label",
        help_text=_("Filter tags that have/don't have a label"),
    )

    content_type = filters.CharFilter(
        field_name="taggeditem__content_type__model",
        help_text=_("Filter tags used for specific content type"),
    )
    content_type__app_label = filters.CharFilter(
        field_name="taggeditem__content_type__app_label",
        help_text=_("Filter tags used for content from specific app"),
    )
    object_id = filters.NumberFilter(
        field_name="taggeditem__object_id",
        help_text=_("Filter tags used for specific object ID"),
    )

    min_usage_count = filters.NumberFilter(
        method="filter_min_usage_count",
        help_text=_("Filter tags used at least X times"),
    )
    max_usage_count = filters.NumberFilter(
        method="filter_max_usage_count",
        help_text=_("Filter tags used at most X times"),
    )
    has_usage = filters.BooleanFilter(
        method="filter_has_usage",
        help_text=_("Filter tags that are/aren't used"),
    )

    most_used = filters.BooleanFilter(
        method="filter_most_used",
        help_text=_("Order tags by usage count (most used first)"),
    )
    unused = filters.BooleanFilter(
        method="filter_unused",
        help_text=_("Filter tags not used anywhere"),
    )

    class Meta:
        model = Tag
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "sort_order": ["exact", "gte", "lte"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "active": ["exact"],
            "translations__label": ["exact", "icontains", "istartswith"],
        }

    def filter_has_label(self, queryset, name, value):
        """Filter tags based on whether they have a label."""
        if value is True:
            return queryset.exclude(
                Q(translations__label__isnull=True)
                | Q(translations__label__exact="")
            )
        elif value is False:
            return queryset.filter(
                Q(translations__label__isnull=True)
                | Q(translations__label__exact="")
            )
        return queryset

    def filter_min_usage_count(self, queryset, name, value):
        """Filter tags with minimum usage count."""
        if value is not None:
            return queryset.annotate(
                usage_count=Count("taggeditem", distinct=True)
            ).filter(usage_count__gte=value)
        return queryset

    def filter_max_usage_count(self, queryset, name, value):
        """Filter tags with maximum usage count."""
        if value is not None:
            return queryset.annotate(
                usage_count=Count("taggeditem", distinct=True)
            ).filter(usage_count__lte=value)
        return queryset

    def filter_has_usage(self, queryset, name, value):
        """Filter tags based on whether they are used."""
        if value is True:
            return queryset.annotate(
                usage_count=Count("taggeditem", distinct=True)
            ).filter(usage_count__gt=0)
        elif value is False:
            return queryset.annotate(
                usage_count=Count("taggeditem", distinct=True)
            ).filter(usage_count=0)
        return queryset

    def filter_most_used(self, queryset, name, value):
        """Order tags by usage count."""
        if value is True:
            return queryset.annotate(
                usage_count=Count("taggeditem", distinct=True)
            ).order_by("-usage_count", "sort_order")
        return queryset

    def filter_unused(self, queryset, name, value):
        """Filter tags not used anywhere."""
        if value is True:
            return queryset.annotate(
                usage_count=Count("taggeditem", distinct=True)
            ).filter(usage_count=0)
        return queryset
