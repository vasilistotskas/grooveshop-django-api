from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from country.models import Country
from region.models import Region


@extend_schema_field(generate_schema_multi_lang(Region))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class RegionSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Region]
):
    translations = TranslatedFieldsFieldExtend(shared_model=Region)
    country = PrimaryKeyRelatedField(queryset=Country.objects.all())

    class Meta:
        model = Region
        fields = (
            "translations",
            "alpha",
            "country",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )


class RegionDetailSerializer(RegionSerializer):
    class Meta(RegionSerializer.Meta):
        fields = (*RegionSerializer.Meta.fields,)


class RegionWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Region]
):
    translations = TranslatedFieldsFieldExtend(shared_model=Region)
    country = PrimaryKeyRelatedField(queryset=Country.objects.all())

    def validate_alpha(self, value):
        if value and len(value) > 10:
            raise serializers.ValidationError(
                _("Region alpha code should be 10 characters or less.")
            )
        return value.upper() if value else value

    def validate(self, data):
        country = data.get("country")
        alpha = data.get("alpha")

        if country and alpha:
            existing = Region.objects.filter(country=country, alpha=alpha)
            if self.instance:
                existing = existing.exclude(pk=self.instance.pk)

            if existing.exists():
                raise serializers.ValidationError(
                    _(
                        "Region with alpha '{alpha}' already exists for {country_alpha_2}"
                    ).format(
                        alpha=alpha,
                        country_alpha_2=country.alpha_2,
                    )
                )

        return data

    class Meta:
        model = Region
        fields = (
            "translations",
            "alpha",
            "country",
            "sort_order",
        )
