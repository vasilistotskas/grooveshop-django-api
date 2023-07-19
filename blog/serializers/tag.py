from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer

from blog.models.post import BlogPost
from blog.models.tag import BlogTag
from core.api.schema import generate_schema_multi_lang


@extend_schema_field(generate_schema_multi_lang(BlogPost))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class BlogTagSerializer(TranslatableModelSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=BlogTag)

    class Meta:
        model = BlogTag
        fields = (
            "translations",
            "id",
            "active",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
            "get_tag_posts_count",
        )
