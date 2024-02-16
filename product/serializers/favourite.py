import importlib
from typing import Dict
from typing import Type

from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.serializers import BaseExpandSerializer
from product.models.favourite import ProductFavourite
from product.models.product import Product

User = get_user_model()


class ProductFavouriteSerializer(BaseExpandSerializer):
    user = PrimaryKeyRelatedField(queryset=User.objects.all(), many=False)
    product = PrimaryKeyRelatedField(queryset=Product.objects.all(), many=False)

    class Meta:
        model = ProductFavourite
        fields = ("id", "user", "product", "created_at", "updated_at", "uuid")

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        user_account_serializer = importlib.import_module(
            "user.serializers.account"
        ).UserAccountSerializer
        product_serializer = importlib.import_module(
            "product.serializers.product"
        ).ProductSerializer
        return {
            "user": user_account_serializer,
            "product": product_serializer,
        }
