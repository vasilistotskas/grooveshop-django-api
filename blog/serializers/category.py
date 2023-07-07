from rest_framework import serializers

from blog.models.category import BlogCategory


class BlogCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = BlogCategory
        fields = (
            "id",
            "name",
            "slug",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
            "main_image_absolute_url",
            "main_image_filename",
        )
