from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from authentication.serializers import AuthenticationSerializer
from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from product.models.product import Product
from product.models.review import ProductReview
from product.serializers.product import ProductSerializer

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(ProductReview))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class ProductReviewSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ProductReview]
):
    translations = TranslatedFieldsFieldExtend(shared_model=ProductReview)
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    product = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ProductReview
        fields = (
            "id",
            "product",
            "user",
            "rate",
            "status",
            "is_published",
            "created_at",
            "updated_at",
            "published_at",
            "uuid",
            "translations",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "published_at",
            "uuid",
        )

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if "product" in rep and instance.product:
            rep["product"] = ProductSerializer(instance.product).data

        if "user" in rep and instance.user:
            rep["user"] = AuthenticationSerializer(instance.user).data

        return rep


class ProductReviewDetailSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ProductReview]
):
    translations = TranslatedFieldsFieldExtend(shared_model=ProductReview)
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    product = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ProductReview
        fields = (
            "id",
            "product",
            "user",
            "rate",
            "status",
            "is_published",
            "created_at",
            "updated_at",
            "published_at",
            "uuid",
            "translations",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "published_at",
            "uuid",
        )

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if "product" in rep and instance.product:
            rep["product"] = ProductSerializer(instance.product).data

        if "user" in rep and instance.user:
            rep["user"] = AuthenticationSerializer(instance.user).data

        return rep


class ProductReviewWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ProductReview]
):
    translations = TranslatedFieldsFieldExtend(shared_model=ProductReview)
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())

    class Meta:
        model = ProductReview
        fields = (
            "product",
            "rate",
            "status",
            "is_published",
            "translations",
        )

    def validate_rate(self, value):
        valid_rates = [choice[0] for choice in ProductReview.rate.field.choices]
        if value not in valid_rates:
            raise serializers.ValidationError(_("Invalid rate value."))
        return value

    def create(self, validated_data):
        user = self.context["request"].user
        validated_data["user"] = user
        return super().create(validated_data)

    def validate(self, attrs):
        user = self.context["request"].user
        product = attrs.get("product")

        if (
            self.instance is None
            and ProductReview.objects.filter(
                user=user, product=product
            ).exists()
        ):
            raise serializers.ValidationError(
                _("You have already reviewed this product.")
            )

        return attrs


class UserProductReviewRequestSerializer(serializers.Serializer):
    product = serializers.IntegerField(
        help_text=_("ID of the product to get user's review for")
    )
