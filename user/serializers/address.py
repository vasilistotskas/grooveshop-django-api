from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from country.models import Country
from region.models import Region
from user.models.address import UserAddress

User = get_user_model()


class UserAddressSerializer(serializers.ModelSerializer[UserAddress]):
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
            "id",
            "user",
            "created_at",
            "updated_at",
            "uuid",
        )


class UserAddressDetailSerializer(UserAddressSerializer):
    class Meta(UserAddressSerializer.Meta):
        fields = (*UserAddressSerializer.Meta.fields,)


class UserAddressWriteSerializer(serializers.ModelSerializer[UserAddress]):
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    country = PrimaryKeyRelatedField(queryset=Country.objects.all())
    region = PrimaryKeyRelatedField(
        queryset=Region.objects.all(), required=False
    )
    phone = PhoneNumberField()
    mobile_phone = PhoneNumberField(required=False)

    def validate_phone(self, value: str) -> str:
        if not value and not self.initial_data.get("mobile_phone"):
            raise serializers.ValidationError(
                _("Either phone or mobile phone is required")
            )
        return value

    def validate(self, data):
        if data.get("is_main"):
            user = data.get("user") or (
                self.instance.user if self.instance else None
            )
            if user:
                existing_main = UserAddress.objects.filter(
                    user=user, is_main=True
                )
                if self.instance:
                    existing_main = existing_main.exclude(pk=self.instance.pk)

                if existing_main.exists():
                    raise serializers.ValidationError(
                        _("A main address already exists for this user")
                    )

        return data

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


class ValidateAddressResponseSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    errors = serializers.DictField(child=serializers.CharField())
    suggestions = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField())
    )


class BulkDeleteAddressesRequestSerializer(serializers.Serializer):
    address_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        help_text=_("List of address IDs to delete"),
    )


class BulkDeleteAddressesResponseSerializer(serializers.Serializer):
    deleted_count = serializers.IntegerField()
    deleted_ids = serializers.ListField(child=serializers.IntegerField())
