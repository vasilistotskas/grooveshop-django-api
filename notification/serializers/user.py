from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from user.serializers.account import UserDetailsSerializer
from notification.models.user import NotificationUser
from notification.serializers.notification import NotificationSerializer


class NotificationUserSerializer(serializers.ModelSerializer[NotificationUser]):
    # `user` is exposed read-only on the read serializer so clients can
    # see which user owns each row; writes must NOT accept a user FK from
    # the client — that's done via NotificationUserWriteSerializer which
    # uses HiddenField(CurrentUserDefault).
    user = PrimaryKeyRelatedField(read_only=True)
    notification = PrimaryKeyRelatedField(read_only=True)

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
            "user",
            "notification",
            "created_at",
            "updated_at",
            "uuid",
        )


class NotificationUserDetailSerializer(NotificationUserSerializer):
    # Detail view replaces the read-only PK `user` on the base serializer
    # with the nested UserDetailsSerializer for richer output.
    user = UserDetailsSerializer(read_only=True)
    notification = NotificationSerializer(read_only=True)

    class Meta(NotificationUserSerializer.Meta):
        pass


class NotificationUserWriteSerializer(
    serializers.ModelSerializer[NotificationUser]
):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
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


class NotificationSuccessResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(
        help_text=_("Whether the operation was successful"),
        required=False,
    )
