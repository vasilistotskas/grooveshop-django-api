from rest_framework import serializers

from blog.models.tag import BlogTag


class BlogTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlogTag
        fields = (
            "id",
            "name",
            "active",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
            "get_tag_posts_count",
        )
