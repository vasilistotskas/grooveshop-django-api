from core.api.serializers import BaseExpandSerializer
from vat.models import Vat


class VatSerializer(BaseExpandSerializer):
    class Meta:
        model = Vat
        fields = (
            "id",
            "value",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )
