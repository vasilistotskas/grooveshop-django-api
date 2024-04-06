from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.utils.serializer_helpers import ReturnDict

from blog.models.category import BlogCategory
from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer


@extend_schema_field(generate_schema_multi_lang(BlogCategory))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class BlogCategorySerializer(TranslatableModelSerializer, BaseExpandSerializer):
    children = serializers.SerializerMethodField()
    translations = TranslatedFieldsFieldExtend(shared_model=BlogCategory)

    def get_children(self, obj: BlogCategory) -> ReturnDict | list:
        if obj.get_children().exists():
            return BlogCategorySerializer(
                obj.get_children(), many=True, context=self.context
            ).data
        return []

    class Meta:
        model = BlogCategory
        fields = (
            "translations",
            "id",
            "slug",
            "children",
            "parent",
            "level",
            "tree_id",
            "absolute_url",
            "recursive_post_count",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
            "main_image_absolute_url",
            "main_image_filename",
        )
