import importlib
from typing import Dict
from typing import override
from typing import Type

from django.contrib.auth import get_user_model
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.serializers import BaseExpandSerializer
from country.models import Country
from region.models import Region
from user.models.address import UserAddress

User = get_user_model()


class UserAddressSerializer(BaseExpandSerializer):
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    country = PrimaryKeyRelatedField(queryset=Country.objects.all())
    region = PrimaryKeyRelatedField(queryset=Region.objects.all())
    phone = PhoneNumberField()
    mobile_phone = PhoneNumberField(required=False)

    class Meta:
        model = UserAddress
        fields = (
            "id",
            "title",
            "first_name",
            "last_name",
            "street",
            "street_number",
            "city",
            "zipcode",
            "floor",
            "location_type",
            "phone",
            "mobile_phone",
            "notes",
            "is_main",
            "user",
            "country",
            "region",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )

    @override
    def get_expand_fields(
        self,
    ) -> Dict[str, Type[serializers.ModelSerializer]]:
        user_account_serializer = importlib.import_module(
            "authentication.serializers"
        ).AuthenticationSerializer
        country_serializer = importlib.import_module("country.serializers").CountrySerializer
        region_serializer = importlib.import_module("region.serializers").RegionSerializer
        return {
            "user": user_account_serializer,
            "country": country_serializer,
            "region": region_serializer,
        }

    @override
    def validate(self, data):
        if self.instance and "is_main" in data and data["is_main"]:
            user = data["user"]
            if UserAddress.objects.filter(user=user, is_main=True).exclude(pk=self.instance.pk):
                raise serializers.ValidationError("A main address already exists for this user")
        return data
