from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from notification.models.user import NotificationUser

User = get_user_model()


class NotificationUserSerializer(serializers.ModelSerializer):
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    notification = PrimaryKeyRelatedField(
        queryset=NotificationUser.objects.all()
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
