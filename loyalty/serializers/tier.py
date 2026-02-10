from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from loyalty.models.tier import LoyaltyTier


@extend_schema_field(generate_schema_multi_lang(LoyaltyTier))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class LoyaltyTierSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[LoyaltyTier]
):
    translations = TranslatedFieldsFieldExtend(shared_model=LoyaltyTier)

    class Meta:
        model = LoyaltyTier
        fields = (
            "id",
            "translations",
            "required_level",
            "points_multiplier",
            "icon",
            "main_image_path",
            "icon_filename",
        )
        read_only_fields = (
            "id",
            "required_level",
            "points_multiplier",
            "main_image_path",
            "icon_filename",
        )
