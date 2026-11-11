from rest_framework import serializers

from product.models.brand import Brand


class BrandSerializer(serializers.ModelSerializer[Brand]):
    class Meta:
        model = Brand
        fields = ("id", "name")
        read_only_fields = fields
