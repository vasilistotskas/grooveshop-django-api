from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from product.models.category import ProductCategory


@extend_schema_field(generate_schema_multi_lang(ProductCategory))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class ProductCategorySerializer(TranslatableModelSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=ProductCategory)

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
