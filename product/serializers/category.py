from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.utils.serializer_helpers import ReturnDict

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from core.utils.serializers import TranslatedFieldExtended
from product.models.category import ProductCategory


@extend_schema_field(generate_schema_multi_lang(ProductCategory))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class ProductCategorySerializer(TranslatableModelSerializer, BaseExpandSerializer):
    children = serializers.SerializerMethodField()
    translations = TranslatedFieldsFieldExtend(shared_model=ProductCategory)

    def get_children(self, obj: ProductCategory) -> ReturnDict | list:
        if obj.get_children().exists():
            return ProductCategorySerializer(obj.get_children(), many=True, context=self.context).data
        return []

    class Meta:
        model = ProductCategory
        fields = (
            "translations",
            "id",
            "slug",
            "children",
            "parent",
            "level",
            "tree_id",
            "seo_title",
            "seo_description",
            "seo_keywords",
            "created_at",
            "updated_at",
            "uuid",
            "category_menu_image_one_path",
            "category_menu_image_two_path",
            "category_menu_main_banner_path",
            "absolute_url",
            "recursive_product_count",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "category_menu_image_one_path",
            "category_menu_image_two_path",
            "category_menu_main_banner_path",
            "absolute_url",
            "recursive_product_count",
        )
