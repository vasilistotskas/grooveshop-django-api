from rest_framework import serializers

from tip.models import Tip


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
