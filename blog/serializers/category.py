from django.utils.translation import gettext_lazy as _
from drf_spectacular.helpers import lazy_serializer
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from blog.models.category import BlogCategory
from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended


@extend_schema_field(generate_schema_multi_lang(BlogCategory))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class BlogCategorySerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[BlogCategory]
):
    translations = TranslatedFieldsFieldExtend(shared_model=BlogCategory)
    post_count = serializers.SerializerMethodField()
    has_children = serializers.SerializerMethodField()

    def get_post_count(self, obj: BlogCategory) -> int:
        return obj.blog_posts.filter(is_published=True).count()

    def get_has_children(self, obj: BlogCategory) -> bool:
        return obj.get_children().exists()

    class Meta:
        model = BlogCategory
        fields = (
            "id",
            "translations",
            "slug",
            "parent",
            "level",
            "sort_order",
            "post_count",
            "has_children",
            "main_image_path",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "level",
            "post_count",
            "has_children",
            "main_image_path",
            "created_at",
            "updated_at",
        )


class BlogCategoryDetailSerializer(BlogCategorySerializer):
    children = serializers.SerializerMethodField()
    ancestors = serializers.SerializerMethodField()
    siblings_count = serializers.SerializerMethodField()
    descendants_count = serializers.SerializerMethodField()
    recursive_post_count = serializers.SerializerMethodField()
    category_path = serializers.SerializerMethodField()

    @extend_schema_field(
        lazy_serializer("blog.serializers.category.BlogCategorySerializer")(
            many=True
        )
    )
    def get_children(self, obj: BlogCategory):
        if obj.get_children().exists():
            return BlogCategorySerializer(
                obj.get_children(), many=True, context=self.context
            ).data
        return []

    @extend_schema_field(
        lazy_serializer("blog.serializers.category.BlogCategorySerializer")(
            many=True
        )
    )
    def get_ancestors(self, obj: BlogCategory):
        ancestors = obj.get_ancestors()
        return BlogCategorySerializer(
            ancestors, many=True, context=self.context
        ).data

    def get_siblings_count(self, obj: BlogCategory) -> int:
        return obj.get_siblings().count()

    def get_descendants_count(self, obj: BlogCategory) -> int:
        return obj.get_descendants().count()

    def get_recursive_post_count(self, obj: BlogCategory) -> int:
        return obj.recursive_post_count

    def get_category_path(self, obj: BlogCategory) -> str:
        ancestors = obj.get_ancestors(include_self=True)
        return " > ".join(
            [
                ancestor.safe_translation_getter("name", any_language=True)
                or "Unnamed"
                for ancestor in ancestors
            ]
        )

    class Meta(BlogCategorySerializer.Meta):
        fields = (
            *BlogCategorySerializer.Meta.fields,
            "children",
            "ancestors",
            "siblings_count",
            "descendants_count",
            "recursive_post_count",
            "category_path",
            "tree_id",
            "uuid",
        )
        read_only_fields = (
            *BlogCategorySerializer.Meta.read_only_fields,
            "children",
            "ancestors",
            "siblings_count",
            "descendants_count",
            "recursive_post_count",
            "category_path",
            "tree_id",
            "uuid",
        )


class BlogCategoryWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[BlogCategory]
):
    translations = TranslatedFieldsFieldExtend(shared_model=BlogCategory)

    def validate_parent(self, value: BlogCategory) -> BlogCategory:
        if value and self.instance:
            if value == self.instance:
                raise serializers.ValidationError(
                    _("Category cannot be its own parent.")
                )
            if value in self.instance.get_descendants():
                raise serializers.ValidationError(
                    _("Category cannot have a descendant as parent.")
                )
        return value

    def validate_slug(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError(_("Slug is required."))

        queryset = BlogCategory.objects.filter(slug=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                _("A post with this slug already exists.")
            )

        return value

    class Meta:
        model = BlogCategory
        fields = (
            "translations",
            "slug",
            "parent",
            "sort_order",
            "image",
        )


class BlogCategoryReorderItemSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text=_("Category ID"))
    sort_order = serializers.IntegerField(help_text=_("New sort order value"))


class BlogCategoryReorderRequestSerializer(serializers.Serializer):
    categories = serializers.ListField(
        child=BlogCategoryReorderItemSerializer(),
        help_text=_("List of categories with new sort orders"),
    )

    def validate_categories(
        self,
        value: list[dict[str, int]],
    ) -> list[dict[str, int]]:
        if not value:
            raise serializers.ValidationError(
                _("At least one category is required.")
            )

        category_ids = [item["id"] for item in value]
        if len(category_ids) != len(set(category_ids)):
            raise serializers.ValidationError(
                _("Duplicate category IDs are not allowed.")
            )

        existing_ids = set(
            BlogCategory.objects.filter(id__in=category_ids).values_list(
                "id", flat=True
            )
        )
        missing_ids = set(category_ids) - existing_ids
        if missing_ids:
            raise serializers.ValidationError(
                _("Categories with IDs %(missing_ids)s do not exist.")
                % {"missing_ids": missing_ids},
            )
        return value


class BlogCategoryReorderResponseSerializer(serializers.Serializer):
    updated_count = serializers.IntegerField(
        help_text=_("Number of categories updated")
    )
    message = serializers.CharField(help_text=_("Success message"))
