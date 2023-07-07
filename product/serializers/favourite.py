from typing import Dict
from typing import Type

from core.api.serializers import BaseExpandSerializer
from product.models.favourite import ProductFavourite
from product.models.product import Product
from product.serializers.product import ProductSerializer
from user.models import UserAccount
from user.serializers.account import UserAccountSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField


class ProductFavouriteSerializer(BaseExpandSerializer):
    user = PrimaryKeyRelatedField(queryset=UserAccount.objects.all(), many=False)
    product = PrimaryKeyRelatedField(queryset=Product.objects.all(), many=False)

    class Meta:
        model = ProductFavourite
        fields = ("id", "user", "product", "created_at", "updated_at", "uuid")

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {
            "user": UserAccountSerializer,
            "product": ProductSerializer,
        }
