from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from product.models.product import Product


@extend_schema_field(generate_schema_multi_lang(Product))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class SearchProductResultSerializer(TranslatableModelSerializer):
    id = serializers.IntegerField()
    slug = serializers.CharField()
    main_image_filename = serializers.CharField()
    absolute_url = serializers.CharField()

    translations = TranslatedFieldsFieldExtend(shared_model=Product)
    search_rank = serializers.FloatField(read_only=True)
    headline = serializers.CharField(read_only=True)
    similarity = serializers.FloatField(read_only=True)

    class Meta:
        model = Product
        fields = (
            "id",
            "slug",
            "main_image_filename",
            "absolute_url",
            "translations",
            "search_rank",
            "headline",
            "similarity",
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["translations"] = {
            language_code: translation
            for language_code, translation in data["translations"].items()
        }
        return data


class SearchProductSerializer(serializers.Serializer):
    results = SearchProductResultSerializer(many=True)
    headlines = serializers.DictField(child=serializers.CharField())
    search_ranks = serializers.DictField(child=serializers.FloatField())
    result_count = serializers.IntegerField()
    similarities = serializers.DictField(child=serializers.FloatField())
