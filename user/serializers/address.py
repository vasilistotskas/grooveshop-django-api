from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from country.models import Country
from region.models import Region
from user.models.address import UserAddress

User = get_user_model()


class UserAddressSerializer(serializers.ModelSerializer):
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

    def validate(self, data):
        if self.instance and "is_main" in data and data["is_main"]:
            user = data["user"]
            if UserAddress.objects.filter(user=user, is_main=True).exclude(
                pk=self.instance.pk
            ):
                raise serializers.ValidationError(
                    _("A main address already exists for this user")
                )
        return data


class UserAddressCreateSerializer(serializers.ModelSerializer):
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    country = PrimaryKeyRelatedField(queryset=Country.objects.all())
    region = PrimaryKeyRelatedField(queryset=Region.objects.all())
    phone = PhoneNumberField()
    mobile_phone = PhoneNumberField(required=False)

    class Meta:
        model = UserAddress
        fields = (
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

    def validate(self, data):
        if data.get("is_main"):
            user = data["user"]
            if UserAddress.objects.filter(user=user, is_main=True).exists():
                raise serializers.ValidationError(
                    _("A main address already exists for this user")
                )
        return data


class UserAddressUpdateSerializer(serializers.ModelSerializer):
    user = PrimaryKeyRelatedField(read_only=True)
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
            "user",
            "created_at",
            "updated_at",
            "uuid",
        )

    def validate(self, data):
        if self.instance and "is_main" in data and data["is_main"]:
            user = self.instance.user
            if (
                UserAddress.objects.filter(user=user, is_main=True)
                .exclude(pk=self.instance.pk)
                .exists()
            ):
                raise serializers.ValidationError(
                    _("A main address already exists for this user")
                )
        return data
