from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.validators import address as address_rules
from country.models import Country
from region.models import Region
from user.models.address import UserAddress

User = get_user_model()


class UserAddressSerializer(serializers.ModelSerializer[UserAddress]):
    user = PrimaryKeyRelatedField(read_only=True)
    country = PrimaryKeyRelatedField(queryset=Country.objects.all())
    # Null for an address in a country without regions — the write
    # serializer accepts that — so the published schema must allow it:
    # the storefront parses this response against that schema, and one
    # region-less address made it reject the whole address list.
    region = PrimaryKeyRelatedField(
        queryset=Region.objects.all(), allow_null=True
    )
    phone = PhoneNumberField()

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
    user = PrimaryKeyRelatedField(read_only=True)
    country = PrimaryKeyRelatedField(queryset=Country.objects.all())
    # Required exactly when the country has regions — not every
    # country does (the full ISO 3166-1 seed's smaller territories
    # mostly don't), so the field itself can't require it
    # unconditionally. ``address_rules.address_update_errors`` below
    # is what actually enforces it, per-country.
    region = PrimaryKeyRelatedField(
        queryset=Region.objects.all(), required=False, allow_null=True
    )
    phone = PhoneNumberField()

    def validate(self, attrs):
        if attrs.get("is_main"):
            user = attrs.get("user") or (
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

        # The same delivery-address rules as checkout, since a saved
        # address is what checkout prefills.
        errors = address_rules.address_update_errors(attrs, self.instance)
        if errors:
            raise serializers.ValidationError(errors)

        return attrs

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
            "notes",
            "is_main",
            "user",
            "country",
            "region",
        )
