from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from product.models.favourite import ProductFavourite
from product.models.product import Product
from product.serializers.product import ProductSerializer

User = get_user_model()


class ProductFavouriteSerializer(serializers.ModelSerializer):
    user = PrimaryKeyRelatedField(queryset=User.objects.all(), many=False)
    product = PrimaryKeyRelatedField(queryset=Product.objects.all(), many=False)

    class Meta:
        model = ProductFavourite
        fields = (
            "id",
            "user",
            "product",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if "product" in rep and instance.product:
            rep["product"] = ProductSerializer(instance.product).data

        return rep
