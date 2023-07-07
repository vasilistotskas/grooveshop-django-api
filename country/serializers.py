from country.models import Country
from rest_framework import serializers


class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = (
            "name",
            "alpha_2",
            "alpha_3",
            "iso_cc",
            "phone_code",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
        )
