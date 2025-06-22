from drf_spectacular.helpers import lazy_serializer
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from product.models.category import ProductCategory


@extend_schema_field(generate_schema_multi_lang(ProductCategory))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class ProductCategorySerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ProductCategory]
):
    translations = TranslatedFieldsFieldExtend(shared_model=ProductCategory)

    class Meta:
        model = ProductCategory
        fields = (
            "id",
            "translations",
            "slug",
            "active",
            "parent",
            "level",
            "tree_id",
            "created_at",
            "updated_at",
            "uuid",
            "recursive_product_count",
        )
        read_only_fields = (
            "id",
            "level",
            "tree_id",
            "created_at",
            "updated_at",
            "uuid",
            "recursive_product_count",
        )


class ProductCategoryDetailSerializer(ProductCategorySerializer):
    children = serializers.SerializerMethodField()

    @extend_schema_field(
        lazy_serializer(
            "product.serializers.category.ProductCategorySerializer"
        )(many=True)
    )
    def get_children(self, obj: ProductCategory):
        if obj.get_children().exists():
            return ProductCategorySerializer(
                obj.get_children(), many=True, context=self.context
            ).data
        return []

    class Meta(ProductCategorySerializer.Meta):
        fields = (
            *ProductCategorySerializer.Meta.fields,
            "children",
            "seo_title",
            "seo_description",
            "seo_keywords",
        )


class ProductCategoryWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ProductCategory]
):
    translations = TranslatedFieldsFieldExtend(shared_model=ProductCategory)

    class Meta:
        model = ProductCategory
        fields = (
            "translations",
            "slug",
            "active",
            "parent",
            "seo_title",
            "seo_description",
            "seo_keywords",
        )
