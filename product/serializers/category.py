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
            return ProductCategorySerializer(
                obj.get_children(), many=True, context=self.context
            ).data
        return []

    class Meta:
        model = ProductCategory
        fields = (
            "translations",
            "id",
            "slug",
            "category_menu_image_one_absolute_url",
            "category_menu_image_one_filename",
            "category_menu_image_two_absolute_url",
            "category_menu_image_two_filename",
            "category_menu_main_banner_absolute_url",
            "category_menu_main_banner_filename",
            "children",
            "parent",
            "level",
            "tree_id",
            "absolute_url",
            "recursive_product_count",
            "seo_title",
            "seo_description",
            "seo_keywords",
            "created_at",
            "updated_at",
            "uuid",
        )
