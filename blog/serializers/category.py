from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer

from blog.models.category import BlogCategory
from core.api.schema import generate_schema_multi_lang


@extend_schema_field(generate_schema_multi_lang(BlogCategory))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class BlogCategorySerializer(TranslatableModelSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=BlogCategory)

    class Meta:
        model = BlogCategory
        fields = (
            "translations",
            "id",
            "slug",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
            "main_image_absolute_url",
            "main_image_filename",
        )
