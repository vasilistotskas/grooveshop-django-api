from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import (
    TranslatableModelSerializer,
)
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from country.models import Country
from country.phone import PhoneMetadata as PhoneMetadataDict
from country.phone import phone_metadata_for_region


@extend_schema_field(generate_schema_multi_lang(Country))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class PhoneMetadataSerializer(serializers.Serializer):
    """Read-only phone-number shape derived from ``phonenumbers``.

    Never a model field — see ``country.phone`` for why. Nested rather
    than flattened onto ``CountrySerializer`` so a country with no
    metadata (see ``get_phone_metadata``) can answer ``null`` for the
    whole group instead of four separately-nullable fields.
    """

    national_number_pattern = serializers.CharField(
        help_text=_(
            "Regular expression the whole national number (no country "
            "code, no leading zero) must match for this country."
        ),
    )
    possible_lengths = serializers.ListField(
        child=serializers.IntegerField(),
        help_text=_("Valid national-number lengths for this country."),
    )
    national_prefix_for_parsing = serializers.CharField(
        allow_null=True,
        help_text=_(
            "Digits a local number is written with but that are not "
            "part of the E.164 number (e.g. Germany's leading '0'). "
            "Null when the country has none — most don't."
        ),
    )
    example_mobile = serializers.CharField(
        allow_null=True,
        help_text=_(
            "A real-shaped example mobile number, national format "
            "(e.g. GR '6912345678', CY '96123456')."
        ),
    )


class CountrySerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Country]
):
    translations = TranslatedFieldsFieldExtend(shared_model=Country)
    phone_metadata = serializers.SerializerMethodField(
        help_text=_(
            "Phone-number validation shape for this country, derived "
            "from Django's own ``phonenumbers`` dependency — never "
            "stored. Null only for a placeholder/reserved alpha-2 code "
            "``phonenumbers`` has no metadata for."
        ),
    )
    has_regions = serializers.BooleanField(
        read_only=True,
        help_text=_(
            "Whether this country has any Region rows. Most of the "
            "full ISO 3166-1 seed doesn't — the storefront uses this "
            "to decide whether the address form's region field is "
            "shown at all for the selected country, rather than "
            "unconditionally requiring one."
        ),
    )

    @extend_schema_field(PhoneMetadataSerializer(allow_null=True))
    def get_phone_metadata(self, obj: Country) -> PhoneMetadataDict | None:
        return phone_metadata_for_region(obj.alpha_2)

    class Meta:
        model = Country
        fields = (
            "translations",
            "alpha_2",
            "alpha_3",
            "iso_cc",
            "phone_code",
            "postal_code_pattern",
            "postal_code_example",
            "phone_metadata",
            "has_regions",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
        )
        read_only_fields = (
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
            "phone_metadata",
            "has_regions",
        )


class CountryDetailSerializer(CountrySerializer):
    regions = PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta(CountrySerializer.Meta):
        fields = (
            *CountrySerializer.Meta.fields,
            "regions",
        )


class CountryWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Country]
):
    translations = TranslatedFieldsFieldExtend(shared_model=Country)

    def validate_alpha_2(self, value: str) -> str:
        if len(value) != 2:
            raise serializers.ValidationError(
                _("Alpha-2 code must be exactly 2 characters.")
            )
        return value.upper()

    def validate_alpha_3(self, value: str) -> str:
        if len(value) != 3:
            raise serializers.ValidationError(
                _("Alpha-3 code must be exactly 3 characters.")
            )
        return value.upper()

    def validate_phone_code(self, value: int) -> int:
        if value is not None and (value <= 0 or value > 9999):
            raise serializers.ValidationError(
                _("Phone code must be between 1 and 9999.")
            )
        return value

    def validate_iso_cc(self, value: int) -> int:
        if value is not None and (value <= 0 or value > 999):
            raise serializers.ValidationError(
                _("ISO country code must be between 1 and 999.")
            )
        return value

    class Meta:
        model = Country
        fields = (
            "translations",
            "alpha_2",
            "alpha_3",
            "iso_cc",
            "phone_code",
            "postal_code_pattern",
            "postal_code_example",
            "sort_order",
        )
        read_only_fields = ("sort_order",)
