from vat.models import Vat
from rest_framework import serializers


class VatSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vat
        fields = (
            "id",
            "value",
            "created_at",
            "updated_at",
            "uuid",
        )
