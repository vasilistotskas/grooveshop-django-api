from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from authentication.serializers import AuthenticationSerializer
from core.api.schema import generate_schema_multi_lang
from product.models.product import Product
from product.models.review import ProductReview
from product.serializers.product import ProductSerializer

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(ProductReview))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class ProductReviewSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer
):
    translations = TranslatedFieldsFieldExtend(shared_model=ProductReview)
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())

    class Meta:
        model = ProductReview
        fields = (
            "translations",
            "id",
            "product",
            "user",
            "rate",
            "status",
            "is_published",
            "created_at",
            "updated_at",
            "published_at",
            "is_visible",
            "uuid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "published_at",
            "is_visible",
            "uuid",
        )

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if "product" in rep and instance.product:
            rep["product"] = ProductSerializer(instance.product).data

        if "user" in rep and instance.user:
            rep["user"] = AuthenticationSerializer(instance.user).data

        return rep

    def create(self, validated_data):
        user = self.context["request"].user
        validated_data["user"] = user
        return super().create(validated_data)


class UserProductReviewRequestSerializer(serializers.Serializer):
    product = serializers.IntegerField(
        help_text=_("ID of the product to get user's review for")
    )
