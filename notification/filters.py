import django_filters

from notification.models.user import NotificationUser


class NotificationUserFilter(django_filters.FilterSet):
    id = django_filters.NumberFilter(field_name="id")
    user = django_filters.NumberFilter(field_name="user")
    notification = django_filters.NumberFilter(field_name="notification")
    seen = django_filters.BooleanFilter(field_name="seen")

    seen_after = django_filters.DateTimeFilter(
        field_name="seen_at", lookup_expr="gte"
    )
    seen_before = django_filters.DateTimeFilter(
        field_name="seen_at", lookup_expr="lte"
    )
    created_after = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr="gte"
    )
    created_before = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr="lte"
    )

    notification_kind = django_filters.CharFilter(
        field_name="notification__kind"
    )
    notification_category = django_filters.CharFilter(
        field_name="notification__category"
    )
    notification_priority = django_filters.CharFilter(
        field_name="notification__priority"
    )

    has_seen_at = django_filters.BooleanFilter(method="filter_has_seen_at")

    class Meta:
        model = NotificationUser
        fields = [
            "id",
            "user",
            "notification",
            "seen",
            "seen_after",
            "seen_before",
            "created_after",
            "created_before",
            "notification_kind",
            "notification_category",
            "notification_priority",
            "has_seen_at",
        ]

    def filter_has_seen_at(self, queryset, name, value):
        if value is True:
            return queryset.filter(seen_at__isnull=False)
        elif value is False:
            return queryset.filter(seen_at__isnull=True)
        return queryset
