import importlib
from typing import Dict
from typing import Type

from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.serializers import BaseExpandSerializer
from notification.models.user import NotificationUser

User = get_user_model()


class NotificationUserSerializer(BaseExpandSerializer):
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    notification = PrimaryKeyRelatedField(queryset=NotificationUser.objects.all())

    class Meta:
        model = NotificationUser
        fields = (
            "id",
            "user",
            "notification",
            "seen",
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        user_account_serializer = importlib.import_module(
            "authentication.serializers"
        ).AuthenticationSerializer
        notification_serializer = importlib.import_module(
            "notification.serializers.notification"
        ).NotificationSerializer
        return {
            "user": user_account_serializer,
            "notification": notification_serializer,
        }


class NotificationUserActionSerializer(serializers.Serializer):
    notification_user_id = serializers.IntegerField()
