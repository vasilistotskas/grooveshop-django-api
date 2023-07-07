from tip.models import Tip
from rest_framework import serializers


class TipSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tip
        fields = (
            "title",
            "content",
            "kind",
            "icon",
            "url",
            "active",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
        )
