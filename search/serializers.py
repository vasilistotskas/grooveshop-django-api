from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from blog.models.post import BlogPost
from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from product.models.product import Product


@extend_schema_field(generate_schema_multi_lang(Product))
class TranslatedFieldsFieldExtendProduct(TranslatedFieldsField):
    pass


class SearchProductResultSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    id = serializers.IntegerField()
    slug = serializers.CharField()
    main_image_filename = serializers.CharField()
    absolute_url = serializers.CharField()

    translations = TranslatedFieldsFieldExtendProduct(shared_model=Product)
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

        request_language = (
            self.context.get("request").query_params.get("language")
            if "request" in self.context
            else None
        )

        if (
            request_language
            and "translations" in data
            and request_language in data["translations"]
        ):
            data["translations"] = {
                request_language: data["translations"][request_language]
            }

        return data


class SearchProductSerializer(serializers.Serializer):
    results = SearchProductResultSerializer(many=True)
    headlines = serializers.DictField(child=serializers.CharField())
    search_ranks = serializers.DictField(child=serializers.FloatField())
    result_count = serializers.IntegerField()
    similarities = serializers.DictField(child=serializers.FloatField())
    distances = serializers.DictField(child=serializers.FloatField())


@extend_schema_field(generate_schema_multi_lang(BlogPost))
class TranslatedFieldsFieldExtendBlogPost(TranslatedFieldsField):
    pass


class SearchBlogPostResultSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    id = serializers.IntegerField()
    slug = serializers.CharField()
    main_image_filename = serializers.CharField()
    absolute_url = serializers.CharField()

    translations = TranslatedFieldsFieldExtendBlogPost(shared_model=BlogPost)
    search_rank = serializers.FloatField(read_only=True)
    headline = serializers.CharField(read_only=True)
    similarity = serializers.FloatField(read_only=True)

    class Meta:
        model = BlogPost
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

        request_language = (
            self.context.get("request").query_params.get("language")
            if "request" in self.context
            else None
        )

        if (
            request_language
            and "translations" in data
            and request_language in data["translations"]
        ):
            data["translations"] = {
                request_language: data["translations"][request_language]
            }

        return data


class SearchBlogPostSerializer(serializers.Serializer):
    results = SearchBlogPostResultSerializer(many=True)
    headlines = serializers.DictField(child=serializers.CharField())
    search_ranks = serializers.DictField(child=serializers.FloatField())
    result_count = serializers.IntegerField()
    similarities = serializers.DictField(child=serializers.FloatField())
    distances = serializers.DictField(child=serializers.FloatField())
