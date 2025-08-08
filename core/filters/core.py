from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters


class TimeStampFilterMixin:
    created_after = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
        help_text=_("Filter items created after this date"),
    )
    created_before = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
        help_text=_("Filter items created before this date"),
    )
    updated_after = filters.DateTimeFilter(
        field_name="updated_at",
        lookup_expr="gte",
        help_text=_("Filter items updated after this date"),
    )
    updated_before = filters.DateTimeFilter(
        field_name="updated_at",
        lookup_expr="lte",
        help_text=_("Filter items updated before this date"),
    )


class UUIDFilterMixin:
    uuid = filters.UUIDFilter(
        field_name="uuid",
        help_text=_("Filter by exact UUID"),
    )


class SortableFilterMixin:
    sort_order = filters.NumberFilter(
        field_name="sort_order",
        help_text=_("Filter by exact sort order"),
    )
    sort_order_min = filters.NumberFilter(
        field_name="sort_order",
        lookup_expr="gte",
        help_text=_("Filter items with sort order greater than or equal to"),
    )
    sort_order_max = filters.NumberFilter(
        field_name="sort_order",
        lookup_expr="lte",
        help_text=_("Filter items with sort order less than or equal to"),
    )


class PublishableFilterMixin:
    is_published = filters.BooleanFilter(
        field_name="is_published",
        help_text=_("Filter by published status"),
    )
    published_after = filters.DateTimeFilter(
        field_name="published_at",
        lookup_expr="gte",
        help_text=_("Filter items published after this date"),
    )
    published_before = filters.DateTimeFilter(
        field_name="published_at",
        lookup_expr="lte",
        help_text=_("Filter items published before this date"),
    )
    currently_published = filters.BooleanFilter(
        method="filter_currently_published",
        help_text=_(
            "Filter items that are currently published (published_at <= now and is_published=True)"
        ),
    )

    def filter_currently_published(self, queryset, name, value):
        if value:
            return queryset.published()
        return queryset


class SoftDeleteFilterMixin:
    is_deleted = filters.BooleanFilter(
        field_name="is_deleted",
        help_text=_("Filter by deleted status"),
    )
    include_deleted = filters.BooleanFilter(
        method="filter_include_deleted",
        help_text=_("Include deleted items in results"),
    )
    deleted_after = filters.DateTimeFilter(
        field_name="deleted_at",
        lookup_expr="gte",
        help_text=_("Filter items deleted after this date"),
    )
    deleted_before = filters.DateTimeFilter(
        field_name="deleted_at",
        lookup_expr="lte",
        help_text=_("Filter items deleted before this date"),
    )

    def filter_include_deleted(self, queryset, name, value):
        if value:
            return queryset.model.objects.all_with_deleted()
        return queryset


class MetaDataFilterMixin:
    metadata_has_key = filters.CharFilter(
        field_name="metadata",
        lookup_expr="has_key",
        help_text=_("Filter items where metadata contains the specified key"),
    )
    metadata_has_keys = filters.CharFilter(
        field_name="metadata",
        lookup_expr="has_keys",
        help_text=_(
            "Filter items where metadata contains all specified keys (comma-separated)"
        ),
        method="filter_metadata_has_keys",
    )
    metadata_has_any_keys = filters.CharFilter(
        field_name="metadata",
        lookup_expr="has_any_keys",
        help_text=_(
            "Filter items where metadata contains any of the specified keys (comma-separated)"
        ),
        method="filter_metadata_has_any_keys",
    )
    metadata_contains = filters.CharFilter(
        method="filter_metadata_contains",
        help_text=_(
            "Filter items where metadata contains the specified JSON (as string)"
        ),
    )
    private_metadata_has_key = filters.CharFilter(
        field_name="private_metadata",
        lookup_expr="has_key",
        help_text=_(
            "Filter items where private metadata contains the specified key"
        ),
    )

    def filter_metadata_has_keys(self, queryset, name, value):
        if value:
            keys = [k.strip() for k in value.split(",")]
            return queryset.filter(metadata__has_keys=keys)
        return queryset

    def filter_metadata_has_any_keys(self, queryset, name, value):
        if value:
            keys = [k.strip() for k in value.split(",")]
            return queryset.filter(metadata__has_any_keys=keys)
        return queryset

    def filter_metadata_contains(self, queryset, name, value):
        if value:
            import json

            try:
                json_data = json.loads(value)
                return queryset.filter(metadata__contains=json_data)
            except json.JSONDecodeError:
                return queryset.none()
        return queryset


class BaseTimeStampFilterSet(TimeStampFilterMixin, filters.FilterSet):
    class Meta:
        abstract = True
        fields = {
            "created_at": ["gte", "lte", "date", "year", "month", "day"],
            "updated_at": ["gte", "lte", "date", "year", "month", "day"],
        }


class BasePublishableTimeStampFilterSet(
    PublishableFilterMixin, TimeStampFilterMixin, filters.FilterSet
):
    class Meta:
        abstract = True
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "published_at": ["gte", "lte", "date"],
            "is_published": ["exact"],
        }


class BaseSoftDeleteTimeStampFilterSet(
    SoftDeleteFilterMixin, TimeStampFilterMixin, filters.FilterSet
):
    class Meta:
        abstract = True
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "deleted_at": ["gte", "lte", "date"],
            "is_deleted": ["exact"],
        }


class BaseFullFilterSet(
    TimeStampFilterMixin,
    UUIDFilterMixin,
    SortableFilterMixin,
    PublishableFilterMixin,
    SoftDeleteFilterMixin,
    MetaDataFilterMixin,
    filters.FilterSet,
):
    class Meta:
        abstract = True
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "published_at": ["gte", "lte", "date"],
            "deleted_at": ["gte", "lte", "date"],
            "is_published": ["exact"],
            "is_deleted": ["exact"],
            "sort_order": ["exact", "gte", "lte"],
            "uuid": ["exact"],
        }
