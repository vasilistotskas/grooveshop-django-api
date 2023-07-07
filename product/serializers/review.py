from typing import Dict
from typing import Type

from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.serializers import BaseExpandSerializer
from product.models.product import Product
from product.models.review import ProductReview
from product.serializers.product import ProductSerializer
from user.models import UserAccount
from user.serializers.account import UserAccountSerializer


class ProductReviewSerializer(BaseExpandSerializer):
    product = PrimaryKeyRelatedField(queryset=Product.objects.all())
    user = PrimaryKeyRelatedField(queryset=UserAccount.objects.all())

    class Meta:
        model = ProductReview
        fields = (
            "id",
            "product",
            "user",
            "comment",
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
