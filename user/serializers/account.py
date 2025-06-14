from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class UsernameUpdateSerializer(serializers.Serializer):
    username = serializers.CharField(
        max_length=150,
        help_text=_("New username"),
    )


class UsernameUpdateResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(
        help_text=_("Success message for username update")
    )


class UserSubscriptionSummaryResponseSerializer(serializers.Serializer):
    total_subscriptions = serializers.IntegerField()
    active_subscriptions = serializers.IntegerField()
    categories = serializers.ListField(child=serializers.CharField())
