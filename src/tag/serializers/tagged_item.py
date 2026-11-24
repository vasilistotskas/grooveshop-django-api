from functools import reduce
from operator import or_

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from core.api.serializers import (
    CONTENT_OBJECT_SERIALIZERS,
    ContentObjectRelatedField,
)
from tag.models import TaggedItem
from tag.serializers.tag import TagDetailSerializer, TagSerializer


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


# The content types the read serializer can draw (``contentObject``): a
# row of any other model would make every read of it a 500. Built from
# the models' own app label and name, so no query runs at import.
TAGGABLE_CONTENT_TYPES = ContentType.objects.filter(
    reduce(
        or_,
        (
            Q(app_label=model._meta.app_label, model=model._meta.model_name)
            for model in CONTENT_OBJECT_SERIALIZERS
        ),
    )
)


class TaggedItemWriteSerializer(serializers.ModelSerializer[TaggedItem]):
    tag_id = serializers.IntegerField(
        write_only=True,
        help_text=_("ID of the tag to assign"),
    )

    def validate_tag_id(self, value: int) -> int:
        from tag.models import Tag

        if not Tag.objects.filter(id=value, active=True).exists():
            raise serializers.ValidationError(
                _("Tag with this ID does not exist or is not active."),
                code="tag_not_found",
            )
        return value

    def validate(self, attrs):
        # A partial update may name only one of the pair: judge the pair
        # the row will hold.
        content_type = attrs.get(
            "content_type", getattr(self.instance, "content_type", None)
        )
        object_id = attrs.get(
            "object_id", getattr(self.instance, "object_id", None)
        )
        model = content_type.model_class() if content_type else None
        if model is None or not model.objects.filter(pk=object_id).exists():
            raise serializers.ValidationError(
                {"object_id": _("The object to tag does not exist.")},
                code="content_object_not_found",
            )
        return attrs

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
        extra_kwargs = {"content_type": {"queryset": TAGGABLE_CONTENT_TYPES}}
