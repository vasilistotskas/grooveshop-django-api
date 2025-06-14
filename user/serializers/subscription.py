from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from user.models.subscription import SubscriptionTopic, UserSubscription


@extend_schema_field(generate_schema_multi_lang(SubscriptionTopic))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class SubscriptionTopicSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer
):
    translations = TranslatedFieldsFieldExtend(shared_model=SubscriptionTopic)
    subscriber_count = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionTopic
        fields = [
            "translations",
            "id",
            "uuid",
            "slug",
            "category",
            "is_active",
            "is_default",
            "requires_confirmation",
            "subscriber_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "uuid",
            "subscriber_count",
            "created_at",
            "updated_at",
        ]

    def get_subscriber_count(self, obj) -> int:
        """Get the count of active subscribers for a topic.

        Handles both model instances and dictionaries.
        """
        if isinstance(obj, dict):
            # When data comes from serializer.data, it's already a dict
            # In test context, we might want to include this information directly
            return obj.get("subscriber_count", 0)

        # For model instances, use the queryset
        return obj.subscribers.filter(
            status=UserSubscription.SubscriptionStatus.ACTIVE
        ).count()


class UserSubscriptionSerializer(serializers.ModelSerializer):
    topic_details = SubscriptionTopicSerializer(source="topic", read_only=True)
    topic = serializers.PrimaryKeyRelatedField(
        queryset=SubscriptionTopic.objects.filter(is_active=True),
    )

    class Meta:
        model = UserSubscription
        fields = [
            "id",
            "user",
            "topic",
            "topic_details",
            "status",
            "subscribed_at",
            "unsubscribed_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "topic_details",
            "subscribed_at",
            "unsubscribed_at",
            "created_at",
            "updated_at",
        ]

    def validate_topic(self, value):
        try:
            topic = SubscriptionTopic.objects.get(id=value.id)
            if not topic.is_active:
                raise serializers.ValidationError(
                    _("This topic is not available for subscription.")
                )
        except SubscriptionTopic.DoesNotExist as e:
            raise serializers.ValidationError(_("Invalid topic ID.")) from e

        if self.instance is None:
            request = self.context.get("request")
            if request and hasattr(request, "user"):
                existing = UserSubscription.objects.filter(
                    user=request.user, topic=value
                ).first()
                if existing:
                    if (
                        existing.status
                        == UserSubscription.SubscriptionStatus.ACTIVE
                    ):
                        raise serializers.ValidationError(
                            _("You are already subscribed to this topic.")
                        )
                    elif (
                        existing.status
                        == UserSubscription.SubscriptionStatus.PENDING
                    ):
                        raise serializers.ValidationError(
                            _(
                                "Your subscription to this topic is pending confirmation."
                            )
                        )

        return value


class BulkSubscriptionSerializer(serializers.Serializer):
    topic_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        help_text=_("List of topic IDs to subscribe/unsubscribe"),
    )
    action = serializers.ChoiceField(
        choices=["subscribe", "unsubscribe"],
        help_text=_("Action to perform on the topics"),
    )

    @staticmethod
    def validate_topic_ids(value):
        existing_ids = set(
            SubscriptionTopic.objects.filter(
                id__in=value, is_active=True
            ).values_list("id", flat=True)
        )

        invalid_ids = set(value) - existing_ids
        if invalid_ids:
            raise serializers.ValidationError(
                _("Invalid or inactive topic IDs: {}").format(list(invalid_ids))
            )

        return value


class UserSubscriptionStatusSerializer(serializers.Serializer):
    subscribed = serializers.ListField(
        child=SubscriptionTopicSerializer(), read_only=True, required=False
    )
    available = serializers.ListField(
        child=SubscriptionTopicSerializer(), read_only=True, required=False
    )

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass
