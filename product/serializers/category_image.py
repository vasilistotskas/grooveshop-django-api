from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from product.models.category import ProductCategory
from product.models.category_image import ProductCategoryImage


@extend_schema_field(generate_schema_multi_lang(ProductCategoryImage))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class ProductCategoryImageSerializer(
    TranslatableModelSerializer,
    serializers.ModelSerializer[ProductCategoryImage],
):
    category = PrimaryKeyRelatedField(queryset=ProductCategory.objects.all())
    translations = TranslatedFieldsFieldExtend(
        shared_model=ProductCategoryImage
    )
    category_name = serializers.CharField(
        source="category.name", read_only=True
    )

    class Meta:
        model = ProductCategoryImage
        fields = (
            "id",
            "category",
            "category_name",
            "image",
            "image_type",
            "active",
            "sort_order",
            "translations",
            "image_path",
            "image_url",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "id",
            "category_name",
            "image_path",
            "image_url",
            "created_at",
            "updated_at",
            "uuid",
        )


class ProductCategoryImageDetailSerializer(ProductCategoryImageSerializer):
    class Meta(ProductCategoryImageSerializer.Meta):
        fields = (
            *ProductCategoryImageSerializer.Meta.fields,
            "translations",
        )


class ProductCategoryImageWriteSerializer(
    TranslatableModelSerializer,
    serializers.ModelSerializer[ProductCategoryImage],
):
    category = PrimaryKeyRelatedField(queryset=ProductCategory.objects.all())
    translations = TranslatedFieldsFieldExtend(
        shared_model=ProductCategoryImage
    )

    class Meta:
        model = ProductCategoryImage
        fields = (
            "category",
            "image",
            "image_type",
            "active",
            "sort_order",
            "translations",
        )

    def validate(self, attrs):
        category = attrs.get("category")
        image_type = attrs.get("image_type")

        if category and image_type:
            existing = ProductCategoryImage.objects.filter(
                category=category, image_type=image_type
            ).exclude(pk=self.instance.pk if self.instance else None)

            if existing.exists():
                raise serializers.ValidationError(
                    f"An image of type '{image_type}' already exists for this category."
                )

        return super().validate(attrs)


class ProductCategoryImageResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = ProductCategoryImageDetailSerializer()


class ProductCategoryImageBulkUpdateSerializer(serializers.Serializer):
    image_ids = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=False
    )
    active = serializers.BooleanField(required=False)
    sort_order = serializers.IntegerField(required=False)

    def validate_image_ids(self, value):
        if not value:
            raise serializers.ValidationError(
                "At least one image ID is required."
            )

        existing_ids = ProductCategoryImage.objects.filter(
            id__in=value
        ).values_list("id", flat=True)

        missing_ids = set(value) - set(existing_ids)
        if missing_ids:
            raise serializers.ValidationError(
                f"Images with IDs {list(missing_ids)} do not exist."
            )

        return value


class ProductCategoryImageBulkResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    updated_count = serializers.IntegerField()
