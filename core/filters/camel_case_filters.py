import re
from djangorestframework_camel_case.util import (
    camel_to_underscore,
    camelize_re,
    underscore_to_camel as underscore_to_camel_callback,
)
from django_filters import rest_framework as filters
from django.utils.translation import gettext_lazy as _
from drf_spectacular.extensions import OpenApiFilterExtension
from drf_spectacular.plumbing import build_parameter_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter


def snake_to_camel(snake_str):
    return re.sub(camelize_re, underscore_to_camel_callback, snake_str)


class CamelCaseFilterMixin:
    def __init__(self, data=None, *args, **kwargs):
        if data is not None:
            data = self._convert_data_to_snake_case(data)

        super().__init__(data, *args, **kwargs)

        self._camel_case_fields = {}

        if hasattr(self, "base_filters"):
            new_filters = {}
            for snake_name, filter_instance in self.base_filters.items():
                camel_name = snake_to_camel(snake_name)

                if camel_name != snake_name:
                    new_filters[camel_name] = filter_instance
                    self._camel_case_fields[camel_name] = snake_name
                else:
                    new_filters[snake_name] = filter_instance

            self.base_filters = new_filters

    def _convert_data_to_snake_case(self, data):
        if not data:
            return data

        if hasattr(data, "getlist"):
            from django.http import QueryDict

            new_data = QueryDict(mutable=True)
            for key in data:
                snake_key = camel_to_underscore(key)
                values = data.getlist(key)
                for value in values:
                    new_data.appendlist(snake_key, value)
            return new_data

        new_data = {}
        for key, value in data.items():
            snake_key = camel_to_underscore(key)
            new_data[snake_key] = value
        return new_data


class CamelCaseTimeStampFilterSet(CamelCaseFilterMixin, filters.FilterSet):
    created_after = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
        help_text="Filter items created after this date",
    )
    created_before = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
        help_text="Filter items created before this date",
    )
    updated_after = filters.DateTimeFilter(
        field_name="updated_at",
        lookup_expr="gte",
        help_text="Filter items updated after this date",
    )
    updated_before = filters.DateTimeFilter(
        field_name="updated_at",
        lookup_expr="lte",
        help_text="Filter items updated before this date",
    )

    class Meta:
        abstract = True


class CamelCasePublishableTimeStampFilterSet(
    CamelCaseFilterMixin, filters.FilterSet
):
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
        """Filter items that are currently published."""
        if value:
            from django.utils import timezone
            from django.db.models import Q

            today = timezone.now()
            return queryset.filter(
                Q(published_at__lte=today, is_published=True)
                | Q(published_at__isnull=True, is_published=True)
            )
        return queryset

    class Meta:
        abstract = True
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "published_at": ["gte", "lte", "date"],
            "is_published": ["exact"],
        }


class CamelCaseFilterExtension(OpenApiFilterExtension):
    target_class = "core.filters.camel_case_filters.CamelCaseFilterMixin"
    priority = 1

    def get_schema_operation_parameters(self, auto_schema, *args, **kwargs):
        parameters = []

        for camel_name, filter_field in self.target.base_filters.items():
            getattr(filter_field, "field_name", camel_name)
            help_text = getattr(filter_field, "help_text", "")

            if isinstance(filter_field, filters.DateTimeFilter):
                schema_type = OpenApiTypes.DATETIME
            elif isinstance(filter_field, filters.BooleanFilter):
                schema_type = OpenApiTypes.BOOL
            elif isinstance(filter_field, filters.NumberFilter):
                schema_type = OpenApiTypes.NUMBER
            elif isinstance(filter_field, filters.UUIDFilter):
                schema_type = OpenApiTypes.UUID
            else:
                schema_type = OpenApiTypes.STR

            parameters.append(
                build_parameter_type(
                    name=camel_name,
                    schema=schema_type,
                    location=OpenApiParameter.QUERY,
                    description=help_text,
                )
            )

        return parameters
