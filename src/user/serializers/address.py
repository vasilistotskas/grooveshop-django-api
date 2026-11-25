from collections.abc import Mapping
from typing import Any

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.validators import address as address_rules
from country.models import Country
from region.models import Region
from shipping.zones import HomeCoverage, destination_errors
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
    shippable = serializers.SerializerMethodField(
        help_text=_(
            "Whether the store delivers to this address at home today "
            "(its shipping zones). An address saved before the store "
            "stopped delivering to its region is kept and reads false."
        )
    )

    class Meta:
        model = UserAddress
        fields = (
            "id",
            "title",
            "first_name",
            "last_name",
            "street",
            "street_number",
            "address_line_2",
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
            "shippable",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "id",
            "user",
            "shippable",
            "created_at",
            "updated_at",
            "uuid",
        )

    def get_shippable(self, obj: UserAddress) -> bool:
        return _coverage(self.context).serves(obj.country_id, obj.region_id)


class UserAddressDetailSerializer(UserAddressSerializer):
    class Meta(UserAddressSerializer.Meta):
        fields = (*UserAddressSerializer.Meta.fields,)


def _coverage(context: Mapping[str, Any]) -> HomeCoverage:
    """The store's home coverage, read once per response: a list of
    addresses shares its serializer context."""
    coverage = context.get("home_coverage")
    if coverage is None:
        coverage = HomeCoverage.load()
        if isinstance(context, dict):
            context["home_coverage"] = coverage
    return coverage


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
                        _("A main address already exists for this user"),
                        code="main_address_exists",
                    )

        # The same delivery-address rules as checkout, since a saved
        # address is what checkout prefills.
        errors = address_rules.address_update_errors(attrs, self.instance)
        if errors:
            raise serializers.ValidationError(errors)
        # And somewhere the store delivers to (its shipping zones): judged
        # only when the write changes the country or the region, so an
        # address kept from before a zone changed can still be edited.
        if errors := self._destination_errors(attrs):
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
            "address_line_2",
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

    def _destination_errors(self, attrs) -> dict:
        """``shipping.zones.destination_errors`` for the destination this
        write gives the address, or none when it keeps the stored one."""
        stored = self.instance
        country = attrs.get("country", stored.country if stored else None)
        region = attrs.get("region", stored.region if stored else None)
        if country is None:
            return {}
        if stored is not None and (
            country.pk == stored.country_id
            and (region.pk if region else None) == stored.region_id
        ):
            return {}
        return destination_errors(country.pk, region.pk if region else None)
