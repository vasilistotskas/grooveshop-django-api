from typing import Dict
from typing import Type

from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from product.models.product import Product
from product.models.review import ProductReview
from product.serializers.product import ProductSerializer
from user.models import UserAccount
from user.serializers.account import UserAccountSerializer


@extend_schema_field(generate_schema_multi_lang(ProductReview))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class ProductReviewSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=ProductReview)
    product = PrimaryKeyRelatedField(queryset=Product.objects.all())
    user = PrimaryKeyRelatedField(queryset=UserAccount.objects.all())

    class Meta:
        model = ProductReview
        fields = (
            "translations",
            "id",
            "product",
            "user",
            "rate",
            "status",
            "created_at",
            "updated_at",
            "published_at",
            "is_published",
            "uuid",
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {
            "product": ProductSerializer,
            "user": UserAccountSerializer,
        }
