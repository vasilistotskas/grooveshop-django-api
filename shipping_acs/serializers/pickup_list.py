from rest_framework import serializers

from shipping_acs.models import AcsPickupList


class AcsPickupListSerializer(serializers.ModelSerializer[AcsPickupList]):
    issued_by_username = serializers.SerializerMethodField()

    class Meta:
        model = AcsPickupList
        fields = (
            "id",
            "pickup_list_no",
            "issued_at",
            "issued_by",
            "issued_by_username",
            "billing_code",
            "voucher_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_issued_by_username(self, obj: AcsPickupList) -> str | None:
        if obj.issued_by_id is None:
            return None
        return getattr(obj.issued_by, "username", None) or getattr(
            obj.issued_by, "email", None
        )
