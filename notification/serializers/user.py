from __future__ import annotations

from adrf.serializers import ModelSerializer as AsyncModelSerializer
from adrf.serializers import Serializer
from django.contrib.auth import get_user_model
from rest_framework import serializers

from core.api.asynchronous.relations import AsyncPrimaryKeyRelatedField
from notification.models.user import NotificationUser

User = get_user_model()


class NotificationUserSerializer(AsyncModelSerializer):
    user = AsyncPrimaryKeyRelatedField(queryset=User.objects.all())
    notification = AsyncPrimaryKeyRelatedField(queryset=NotificationUser.objects.all())

    class Meta:
        model = NotificationUser
        fields = (
            "id",
            "user",
            "notification",
            "seen",
        )


class NotificationUserActionSerializer(Serializer):
    notification_user_id = serializers.IntegerField()
