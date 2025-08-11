from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from core.api.serializers import ContentObjectRelatedField
from tag.models import TaggedItem
from tag.serializers.tag import TagSerializer, TagDetailSerializer


class TaggedItemSerializer(serializers.ModelSerializer[TaggedItem]):
    tag = TagSerializer(read_only=True)
    content_object = ContentObjectRelatedField(read_only=True)
    content_type_name = serializers.CharField(
        source="content_type.model",
        read_only=True,
        help_text=_("Name of the content type"),
    )

    class Meta:
        model = TaggedItem
        fields = [
            "id",
            "tag",
            "content_type",
            "content_type_name",
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


class TaggedItemDetailSerializer(TaggedItemSerializer):
    tag = TagDetailSerializer(read_only=True)

    class Meta(TaggedItemSerializer.Meta):
        fields = TaggedItemSerializer.Meta.fields


class TaggedItemWriteSerializer(serializers.ModelSerializer[TaggedItem]):
    tag_id = serializers.IntegerField(
        write_only=True,
        help_text=_("ID of the tag to assign"),
    )

    def validate_tag_id(self, value: int) -> int:
        from tag.models import Tag

        if not Tag.objects.filter(id=value, active=True).exists():
            raise serializers.ValidationError(
                _("Tag with this ID does not exist or is not active.")
            )
        return value

    def create(self, validated_data):
        from tag.models import Tag

        tag_id = validated_data.pop("tag_id")
        tag = Tag.objects.get(id=tag_id)
        validated_data["tag"] = tag
        return super().create(validated_data)

    class Meta:
        model = TaggedItem
        fields = (
            "tag_id",
            "content_type",
            "object_id",
        )
