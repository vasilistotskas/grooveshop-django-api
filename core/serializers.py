from rest_framework import serializers

from core.models import Settings


class SettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Settings
        fields = ["key", "value", "value_type", "description", "is_public"]
