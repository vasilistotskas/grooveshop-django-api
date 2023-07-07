from pay_way.models import PayWay
from rest_framework import serializers


class PayWaySerializer(serializers.ModelSerializer):
    class Meta:
        model = PayWay
        fields = (
            "id",
            "name",
            "active",
            "cost",
            "free_for_order_amount",
            "icon",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
            "icon_absolute_url",
            "icon_filename",
        )
