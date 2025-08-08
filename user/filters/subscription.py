from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin
from user.models.subscription import SubscriptionTopic, UserSubscription


class SubscriptionTopicFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    category = filters.ChoiceFilter(
        choices=SubscriptionTopic.TopicCategory.choices,
        help_text=_("Filter by topic category"),
    )
    is_active = filters.BooleanFilter(
        help_text=_("Filter by active status"),
    )
    is_default = filters.BooleanFilter(
        help_text=_("Filter by default subscription status"),
    )
    requires_confirmation = filters.BooleanFilter(
        help_text=_("Filter by confirmation requirement"),
    )

    slug = filters.CharFilter(
        lookup_expr="icontains",
        help_text=_("Filter by slug (partial match)"),
    )
    slug_exact = filters.CharFilter(
        field_name="slug",
        lookup_expr="exact",
        help_text=_("Filter by exact slug"),
    )

    name = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by name (partial match)"),
    )
    description = filters.CharFilter(
        field_name="translations__description",
        lookup_expr="icontains",
        help_text=_("Filter by description (partial match)"),
    )

    # Custom filter methods
    has_subscribers = filters.BooleanFilter(
        method="filter_has_subscribers",
        help_text=_("Filter topics that have subscribers"),
    )

    class Meta:
        model = SubscriptionTopic
        fields = {
            "id": ["exact", "in"],
            "category": ["exact"],
            "is_active": ["exact"],
            "is_default": ["exact"],
            "requires_confirmation": ["exact"],
            "slug": ["exact", "icontains"],
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
        }

    def filter_has_subscribers(self, queryset, name, value):
        if value is True:
            return queryset.filter(subscribers__isnull=False).distinct()
        elif value is False:
            return queryset.filter(subscribers__isnull=True)
        return queryset


class UserSubscriptionFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    topic = filters.ModelChoiceFilter(
        queryset=SubscriptionTopic.objects.filter(is_active=True),
        help_text=_("Filter by subscription topic"),
    )
    status = filters.ChoiceFilter(
        choices=UserSubscription.SubscriptionStatus.choices,
        help_text=_("Filter by subscription status"),
    )

    topic_category = filters.ChoiceFilter(
        field_name="topic__category",
        choices=SubscriptionTopic.TopicCategory.choices,
        help_text=_("Filter by topic category"),
    )

    subscribed_after = filters.DateTimeFilter(
        field_name="subscribed_at",
        lookup_expr="gte",
        help_text=_("Filter subscriptions created after this date"),
    )
    subscribed_before = filters.DateTimeFilter(
        field_name="subscribed_at",
        lookup_expr="lte",
        help_text=_("Filter subscriptions created before this date"),
    )
    unsubscribed_after = filters.DateTimeFilter(
        field_name="unsubscribed_at",
        lookup_expr="gte",
        help_text=_("Filter subscriptions unsubscribed after this date"),
    )
    unsubscribed_before = filters.DateTimeFilter(
        field_name="unsubscribed_at",
        lookup_expr="lte",
        help_text=_("Filter subscriptions unsubscribed before this date"),
    )

    topic_slug = filters.CharFilter(
        field_name="topic__slug",
        lookup_expr="icontains",
        help_text=_("Filter by topic slug (partial match)"),
    )
    topic_slug_exact = filters.CharFilter(
        field_name="topic__slug",
        lookup_expr="exact",
        help_text=_("Filter by exact topic slug"),
    )

    topic_name = filters.CharFilter(
        field_name="topic__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by topic name (partial match)"),
    )
    topic_description = filters.CharFilter(
        field_name="topic__translations__description",
        lookup_expr="icontains",
        help_text=_("Filter by topic description (partial match)"),
    )

    # Custom filter methods
    is_confirmed = filters.BooleanFilter(
        method="filter_is_confirmed",
        help_text=_("Filter by confirmation status"),
    )
    has_metadata = filters.BooleanFilter(
        method="filter_has_metadata",
        help_text=_("Filter subscriptions that have metadata"),
    )

    class Meta:
        model = UserSubscription
        fields = {
            "id": ["exact", "in"],
            "topic": ["exact"],
            "status": ["exact"],
            "subscribed_at": ["gte", "lte", "date"],
            "unsubscribed_at": ["gte", "lte", "date"],
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
        }

    def filter_is_confirmed(self, queryset, name, value):
        if value is True:
            return queryset.filter(confirmation_token="")
        elif value is False:
            return queryset.exclude(confirmation_token="")
        return queryset

    def filter_has_metadata(self, queryset, name, value):
        if value is True:
            return queryset.exclude(metadata={})
        elif value is False:
            return queryset.filter(metadata={})
        return queryset
