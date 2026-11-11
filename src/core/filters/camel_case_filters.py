from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from djangorestframework_camel_case.util import camel_to_underscore

from core.filters.core import TimeStampFilterMixin


class CamelCaseFilterMixin:
    """Accept camelCase query parameters for snake_case filters.

    Only the incoming ``data`` is converted. The filters themselves stay
    keyed by their snake_case names: ``BaseFilterSet.__init__`` copies
    the class's ``base_filters`` into ``self.filters``, and that copy is
    all a filterset reads afterwards.
    """

    def __init__(self, data=None, *args, **kwargs):
        if data is not None:
            data = self._convert_data_to_snake_case(data)

        super().__init__(data, *args, **kwargs)

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


class CamelCaseTimeStampFilterSet(CamelCaseFilterMixin, TimeStampFilterMixin):
    # The four timestamp filters come from ``TimeStampFilterMixin`` rather
    # than being restated here. They used to be copied into both classes
    # below, and that copy was load-bearing: the mixin was a plain class,
    # so django-filter dropped its declarations and only the copies ever
    # reached a request.
    class Meta:
        abstract = True


class CamelCasePublishableTimeStampFilterSet(
    CamelCaseFilterMixin, TimeStampFilterMixin
):
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
            from django.db.models import Q
            from django.utils import timezone

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
