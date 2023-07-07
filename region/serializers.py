from typing import Dict
from typing import Type

from core.api.serializers import BaseExpandSerializer
from country.models import Country
from country.serializers import CountrySerializer
from region.models import Region
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField


class RegionSerializer(BaseExpandSerializer):
    alpha_2 = PrimaryKeyRelatedField(queryset=Country.objects.all())

    class Meta:
        model = Region
        fields = (
            "alpha",
            "alpha_2",
            "name",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {
            "alpha_2": CountrySerializer,
        }
