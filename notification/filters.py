from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin
from notification.models.user import NotificationUser


class NotificationUserFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    user = filters.NumberFilter(
        field_name="user__id", help_text=_("Filter by user ID")
    )
    notification = filters.NumberFilter(
        field_name="notification__id", help_text=_("Filter by notification ID")
    )
    seen = filters.BooleanFilter(
        field_name="seen", help_text=_("Filter by seen status")
    )

    user__email = filters.CharFilter(
        field_name="user__email",
        lookup_expr="icontains",
        help_text=_("Filter by user email (case-insensitive)"),
    )
    user__first_name = filters.CharFilter(
        field_name="user__first_name",
        lookup_expr="icontains",
        help_text=_("Filter by user first name (case-insensitive)"),
    )
    user__last_name = filters.CharFilter(
        field_name="user__last_name",
        lookup_expr="icontains",
        help_text=_("Filter by user last name (case-insensitive)"),
    )
    user__is_active = filters.BooleanFilter(
        field_name="user__is_active",
        help_text=_("Filter by user active status"),
    )
    user__is_staff = filters.BooleanFilter(
        field_name="user__is_staff",
        help_text=_("Filter by user staff status"),
    )

    seen_after = filters.DateTimeFilter(
        field_name="seen_at",
        lookup_expr="gte",
        help_text=_("Filter notifications seen after this date"),
    )
    seen_before = filters.DateTimeFilter(
        field_name="seen_at",
        lookup_expr="lte",
        help_text=_("Filter notifications seen before this date"),
    )
    has_seen_at = filters.BooleanFilter(
        method="filter_has_seen_at",
        help_text=_(
            "Filter notifications that have been seen (true) or not seen (false)"
        ),
    )

    notification_kind = filters.CharFilter(
        field_name="notification__kind",
        help_text=_("Filter by notification kind"),
    )
    notification__kind = filters.CharFilter(
        field_name="notification__kind",
        help_text=_("Filter by notification kind"),
    )
    notification__category = filters.CharFilter(
        field_name="notification__category",
        help_text=_("Filter by notification category"),
    )
    notification__priority = filters.CharFilter(
        field_name="notification__priority",
        help_text=_("Filter by notification priority"),
    )
    notification__type = filters.CharFilter(
        field_name="notification__notification_type",
        lookup_expr="icontains",
        help_text=_("Filter by notification type (case-insensitive)"),
    )
    notification__link = filters.CharFilter(
        field_name="notification__link",
        lookup_expr="icontains",
        help_text=_("Filter by notification link (case-insensitive)"),
    )

    notification__title = filters.CharFilter(
        field_name="notification__translations__title",
        lookup_expr="icontains",
        help_text=_("Filter by notification title (case-insensitive)"),
    )
    notification__message = filters.CharFilter(
        field_name="notification__translations__message",
        lookup_expr="icontains",
        help_text=_("Filter by notification message (case-insensitive)"),
    )

    notification__is_expired = filters.BooleanFilter(
        method="filter_notification_is_expired",
        help_text=_("Filter by notification expiry status"),
    )
    notification__expires_after = filters.DateTimeFilter(
        field_name="notification__expiry_date",
        lookup_expr="gte",
        help_text=_("Filter notifications expiring after this date"),
    )
    notification__expires_before = filters.DateTimeFilter(
        field_name="notification__expiry_date",
        lookup_expr="lte",
        help_text=_("Filter notifications expiring before this date"),
    )

    unseen_only = filters.BooleanFilter(
        method="filter_unseen_only",
        help_text=_("Filter only unseen notifications"),
    )
    seen_only = filters.BooleanFilter(
        method="filter_seen_only", help_text=_("Filter only seen notifications")
    )
    recent_notifications = filters.BooleanFilter(
        method="filter_recent_notifications",
        help_text=_("Filter notifications from the last 7 days"),
    )
    high_priority = filters.BooleanFilter(
        method="filter_high_priority",
        help_text=_("Filter high priority notifications"),
    )

    user_ids = filters.CharFilter(
        method="filter_user_ids",
        help_text=_("Filter by multiple user IDs (comma-separated)"),
    )
    notification_ids = filters.CharFilter(
        method="filter_notification_ids",
        help_text=_("Filter by multiple notification IDs (comma-separated)"),
    )

    class Meta:
        model = NotificationUser
        fields = {
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
            "id": ["exact", "in"],
            "seen": ["exact"],
            "seen_at": ["gte", "lte", "date"],
            "user": ["exact"],
            "notification": ["exact"],
        }

    def filter_has_seen_at(self, queryset, name, value):
        """Filter notifications based on whether they have been seen."""
        if value is True:
            return queryset.filter(seen_at__isnull=False)
        elif value is False:
            return queryset.filter(seen_at__isnull=True)
        return queryset

    def filter_notification_is_expired(self, queryset, name, value):
        """Filter notifications based on expiry status."""
        from django.utils import timezone

        now = timezone.now()
        if value is True:
            return queryset.filter(
                notification__expiry_date__isnull=False,
                notification__expiry_date__lt=now,
            )
        elif value is False:
            return queryset.filter(
                Q(notification__expiry_date__isnull=True)
                | Q(notification__expiry_date__gte=now)
            )
        return queryset

    def filter_unseen_only(self, queryset, name, value):
        """Filter only unseen notifications."""
        if value is True:
            return queryset.filter(seen=False)
        return queryset

    def filter_seen_only(self, queryset, name, value):
        """Filter only seen notifications."""
        if value is True:
            return queryset.filter(seen=True)
        return queryset

    def filter_recent_notifications(self, queryset, name, value):
        """Filter notifications from the last 7 days."""
        if value is True:
            from django.utils import timezone
            from datetime import timedelta

            seven_days_ago = timezone.now() - timedelta(days=7)
            return queryset.filter(notification__created_at__gte=seven_days_ago)
        return queryset

    def filter_high_priority(self, queryset, name, value):
        """Filter high priority notifications."""
        if value is True:
            from notification.enum import NotificationPriorityEnum

            return queryset.filter(
                notification__priority=NotificationPriorityEnum.HIGH
            )
        return queryset

    def filter_user_ids(self, queryset, name, value):
        """Filter by multiple user IDs."""
        if value:
            try:
                user_ids = [
                    int(id.strip()) for id in value.split(",") if id.strip()
                ]
                return queryset.filter(user__id__in=user_ids)
            except ValueError:
                return queryset.none()
        return queryset

    def filter_notification_ids(self, queryset, name, value):
        """Filter by multiple notification IDs."""
        if value:
            try:
                notification_ids = [
                    int(id.strip()) for id in value.split(",") if id.strip()
                ]
                return queryset.filter(notification__id__in=notification_ids)
            except ValueError:
                return queryset.none()
        return queryset
