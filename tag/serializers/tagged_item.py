from rest_framework import serializers

from core.api.serializers import ContentObjectRelatedField
from tag.models import TaggedItem
from tag.serializers.tag import TagSerializer


class TaggedItemSerializer(serializers.ModelSerializer[TaggedItem]):
    tag = TagSerializer(read_only=True)
    content_object = ContentObjectRelatedField(read_only=True)

    class Meta:
        model = TaggedItem
        fields = [
            "id",
            "tag",
            "content_type",
            "object_id",
            "content_object",
            "created_at",
            "updated_at",
            "uuid",
        ]
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )
