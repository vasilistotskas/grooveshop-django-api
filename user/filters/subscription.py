import django_filters

from user.models.subscription import SubscriptionTopic, UserSubscription


class SubscriptionTopicFilter(django_filters.FilterSet):
    category = django_filters.ChoiceFilter(
        choices=SubscriptionTopic.TopicCategory.choices
    )
    is_active = django_filters.BooleanFilter()
    is_default = django_filters.BooleanFilter()
    requires_confirmation = django_filters.BooleanFilter()

    slug = django_filters.CharFilter(lookup_expr="icontains")
    slug_exact = django_filters.CharFilter(
        field_name="slug", lookup_expr="exact"
    )

    created_after = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr="gte"
    )
    created_before = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr="lte"
    )
    updated_after = django_filters.DateTimeFilter(
        field_name="updated_at", lookup_expr="gte"
    )
    updated_before = django_filters.DateTimeFilter(
        field_name="updated_at", lookup_expr="lte"
    )

    name = django_filters.CharFilter(
        field_name="translations__name", lookup_expr="icontains"
    )
    description = django_filters.CharFilter(
        field_name="translations__description", lookup_expr="icontains"
    )

    class Meta:
        model = SubscriptionTopic
        fields = [
            "category",
            "is_active",
            "is_default",
            "requires_confirmation",
            "slug",
            "slug_exact",
            "created_after",
            "created_before",
            "updated_after",
            "updated_before",
            "name",
            "description",
        ]


class UserSubscriptionFilter(django_filters.FilterSet):
    topic = django_filters.ModelChoiceFilter(
        queryset=SubscriptionTopic.objects.filter(is_active=True)
    )
    status = django_filters.ChoiceFilter(
        choices=UserSubscription.SubscriptionStatus.choices
    )

    topic__category = django_filters.ChoiceFilter(
        field_name="topic__category",
        choices=SubscriptionTopic.TopicCategory.choices,
    )

    subscribed_after = django_filters.DateTimeFilter(
        field_name="subscribed_at", lookup_expr="gte"
    )
    subscribed_before = django_filters.DateTimeFilter(
        field_name="subscribed_at", lookup_expr="lte"
    )
    unsubscribed_after = django_filters.DateTimeFilter(
        field_name="unsubscribed_at", lookup_expr="gte"
    )
    unsubscribed_before = django_filters.DateTimeFilter(
        field_name="unsubscribed_at", lookup_expr="lte"
    )
    created_after = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr="gte"
    )
    created_before = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr="lte"
    )
    updated_after = django_filters.DateTimeFilter(
        field_name="updated_at", lookup_expr="gte"
    )
    updated_before = django_filters.DateTimeFilter(
        field_name="updated_at", lookup_expr="lte"
    )

    topic__slug = django_filters.CharFilter(
        field_name="topic__slug", lookup_expr="icontains"
    )
    topic__slug_exact = django_filters.CharFilter(
        field_name="topic__slug", lookup_expr="exact"
    )

    topic_name = django_filters.CharFilter(
        field_name="topic__translations__name", lookup_expr="icontains"
    )
    topic_description = django_filters.CharFilter(
        field_name="topic__translations__description", lookup_expr="icontains"
    )

    class Meta:
        model = UserSubscription
        fields = [
            "topic",
            "status",
            "topic__category",
            "subscribed_after",
            "subscribed_before",
            "unsubscribed_after",
            "unsubscribed_before",
            "created_after",
            "created_before",
            "updated_after",
            "updated_before",
            "topic__slug",
            "topic__slug_exact",
            "topic_name",
            "topic_description",
        ]
