from typing import Dict
from typing import Type

from django.contrib.auth import get_user_model
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.serializers import BaseExpandSerializer
from country.models import Country
from country.serializers import CountrySerializer
from region.models import Region
from region.serializers import RegionSerializer
from user.models.address import UserAddress
from user.serializers.account import UserAccountSerializer

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
            "created_at",
            "updated_at",
            "uuid",
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
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {
            "user": UserAccountSerializer,
            "country": CountrySerializer,
            "region": RegionSerializer,
        }

    def validate(self, data):
        if self.instance and "is_main" in data and data["is_main"]:
            user = data["user"]
            if UserAddress.objects.filter(user=user, is_main=True).exclude(
                pk=self.instance.pk
            ):
                raise serializers.ValidationError(
                    "A main address already exists for this user"
                )
        return data
