from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from authentication.serializers import AuthenticationSerializer
from notification.models.user import NotificationUser
from notification.serializers.notification import NotificationSerializer

User = get_user_model()


class NotificationUserSerializer(serializers.ModelSerializer[NotificationUser]):
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    notification = PrimaryKeyRelatedField(
        queryset=NotificationUser.objects.none()
    )

    class Meta:
        model = NotificationUser
        fields = (
            "id",
            "user",
            "notification",
            "seen",
            "seen_at",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from notification.models.notification import Notification  # noqa: PLC0415, I001

        self.fields["notification"].queryset = Notification.objects.all()


class NotificationUserDetailSerializer(NotificationUserSerializer):
    user = AuthenticationSerializer(read_only=True)
    notification = NotificationSerializer(read_only=True)

    class Meta(NotificationUserSerializer.Meta):
        fields = (*NotificationUserSerializer.Meta.fields,)


class NotificationUserWriteSerializer(
    serializers.ModelSerializer[NotificationUser]
):
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    notification = PrimaryKeyRelatedField(
        queryset=NotificationUser.objects.none()
    )

    class Meta:
        model = NotificationUser
        fields = (
            "user",
            "notification",
            "seen",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from notification.models.notification import Notification  # noqa: PLC0415, I001

        self.fields["notification"].queryset = Notification.objects.all()

    def validate(self, attrs):
        user = attrs.get("user")
        notification = attrs.get("notification")

        if (
            self.instance is None
            and user
            and notification
            and NotificationUser.objects.filter(
                user=user, notification=notification
            ).exists()
        ):
            raise serializers.ValidationError(
                _("This user already has this notification.")
            )
        return attrs


class NotificationUserActionSerializer(serializers.Serializer):
    notification_user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text=_("List of notification user IDs to mark as seen/unseen"),
    )


class NotificationCountResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField(
        help_text=_("Number of unseen notifications")
    )


class NotificationInfoResponseSerializer(serializers.Serializer):
    info = serializers.CharField(
        help_text=_("Information message about notifications")
    )


class NotificationSuccessResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(
        help_text=_("Whether the operation was successful")
    )
