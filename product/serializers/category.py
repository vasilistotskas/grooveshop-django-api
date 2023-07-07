from product.models.category import ProductCategory
from rest_framework import serializers


class ProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = (
            "id",
            "name",
            "slug",
            "description",
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
