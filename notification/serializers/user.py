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
    # ``notification`` is read-only: rows are created server-side when a
    # notification is delivered, and a client must not be able to re-point an
    # existing row to another notification and read its content (IDOR). Only
    # the ``seen`` flag is client-writable (on the caller's own rows).
    notification = PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = NotificationUser
        fields = (
            "user",
            "notification",
            "seen",
        )


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
