"""Reusable filter mixins.

**Every mixin here must subclass ``FilterSet``.** django-filter collects
declared filters in ``FilterSetMetaclass.get_declared_filters``, which
reads the class's own ``attrs`` and then the bases that already carry a
``declared_filters`` dict. A plain-class mixin has neither, so its
``Filter`` attributes are silently dropped — no error, no warning, and
the parameter simply never exists.

That is what happened here. Every mixin in this module was a plain
class, so `?metadataHasKey=promo` and every other alias below answered
200 with the whole unfiltered list, and the two staff-gated filters read
as authorization while doing nothing at all. `schema.yml` carried zero
occurrences of the affected names. The camel-case FilterSets appeared to
work only because they restated the same filters verbatim in their own
class bodies — the duplication was load-bearing.

Deleted rather than repaired, because the layer cannot express them:

* ``SoftDeleteFilterMixin`` — ``is_deleted`` / ``include_deleted`` /
  ``deleted_after`` / ``deleted_before``. The soft-delete managers
  exclude deleted rows in ``get_queryset()``, so by the time a filter
  runs the rows are already gone and none of the four can match.
  ``filter_include_deleted`` tried to widen with
  ``queryset.model.objects.all_with_deleted()``, which (a) discards
  every filter applied before it and (b) does not exist on
  ``ProductManager`` — the only model that used the mixin — so it would
  have raised ``AttributeError`` for a staff caller the moment it went
  live. Which rows a soft-delete model exposes is a choice about the
  BASE queryset, and belongs in the viewset's ``get_queryset``.
* ``PublishableFilterMixin`` and the four ``Base*FilterSet`` classes —
  zero references outside this module, measured before removal.
"""

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from tenant.membership import is_store_staff


class TimeStampFilterMixin(filters.FilterSet):
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


class UUIDFilterMixin(filters.FilterSet):
    uuid = filters.UUIDFilter(
        field_name="uuid",
        help_text=_("Filter by exact UUID"),
    )


class SortableFilterMixin(filters.FilterSet):
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


class MetaDataFilterMixin(filters.FilterSet):
    metadata_has_key = filters.CharFilter(
        field_name="metadata",
        lookup_expr="has_key",
        help_text=_("Filter items where metadata contains the specified key"),
    )
    metadata_has_keys = filters.CharFilter(
        help_text=_(
            "Filter items where metadata contains all specified keys "
            "(comma-separated)"
        ),
        method="filter_metadata_has_keys",
    )
    metadata_has_any_keys = filters.CharFilter(
        help_text=_(
            "Filter items where metadata contains any of the specified keys "
            "(comma-separated)"
        ),
        method="filter_metadata_has_any_keys",
    )
    metadata_contains = filters.CharFilter(
        method="filter_metadata_contains",
        help_text=_(
            "Filter items where metadata contains the specified JSON "
            "(as string)"
        ),
    )
    private_metadata_has_key = filters.CharFilter(
        method="filter_private_metadata_has_key",
        help_text=_(
            "Filter items where private metadata contains the specified key "
            "(staff only)"
        ),
    )

    def filter_private_metadata_has_key(self, queryset, name, value):
        request = getattr(self, "request", None)
        if value and request and is_store_staff(getattr(request, "user", None)):
            return queryset.filter(private_metadata__has_key=value)
        return queryset

    def filter_metadata_has_keys(self, queryset, name, value):
        if value:
            keys = [k.strip() for k in value.split(",") if k.strip()]
            if keys:
                return queryset.filter(metadata__has_keys=keys)
        return queryset

    def filter_metadata_has_any_keys(self, queryset, name, value):
        if value:
            keys = [k.strip() for k in value.split(",") if k.strip()]
            if keys:
                return queryset.filter(metadata__has_any_keys=keys)
        return queryset

    def filter_metadata_contains(self, queryset, name, value):
        if not value:
            return queryset
        import json

        try:
            json_data = json.loads(value)
        except json.JSONDecodeError:
            return queryset.none()
        return queryset.filter(metadata__contains=json_data)
