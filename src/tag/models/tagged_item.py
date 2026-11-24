import logging
from functools import reduce
from operator import or_

from django.contrib.contenttypes.fields import (
    GenericForeignKey,
    GenericRelation,
)
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel, UUIDModel
from tag.managers import TaggedItemManager
from tag.models.tag import Tag

logger = logging.getLogger(__name__)

CONTENT_OBJECT_NOT_FOUND = "content_object_not_found"
CONTENT_OBJECT_NOT_FOUND_MESSAGE = _("The object to tag does not exist.")


def servable_content_types() -> Q:
    """The content types the API can draw as ``contentObject``.

    A row of any other model would make every read of it a 500. Matched
    on app label and model name, never ``get_for_model``: contenttypes
    lives in every schema, and the manager's process-wide cache can hand
    back another schema's id. Imported when called, since the registry
    imports the product models, which import this module.
    """
    from core.api.serializers import CONTENT_OBJECT_SERIALIZERS

    return reduce(
        or_,
        (
            Q(app_label=model._meta.app_label, model=model._meta.model_name)
            for model in CONTENT_OBJECT_SERIALIZERS
        ),
    )


def content_object_exists(
    content_type: ContentType | None, object_id: int | None
) -> bool:
    model = content_type.model_class() if content_type else None
    return (
        model is not None
        and object_id is not None
        and model._default_manager.filter(pk=object_id).exists()
    )


class TaggedItem(TimeStampMixinModel, UUIDModel):
    tag = models.ForeignKey(
        Tag, on_delete=models.CASCADE, verbose_name=_("Tag")
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        verbose_name=_("Content Type"),
        limit_choices_to=servable_content_types,
    )
    object_id = models.PositiveIntegerField(verbose_name=_("Object ID"))
    content_object = GenericForeignKey()

    objects: TaggedItemManager = TaggedItemManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Tagged Item")
        verbose_name_plural = _("Tagged Items")
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["content_type", "object_id"],
                name="tagged_item_content_obj_ix",
            ),
            BTreeIndex(fields=["tag"], name="tagged_item_tag_ix"),
            BTreeIndex(fields=["object_id"], name="tagged_item_object_id_ix"),
        ]

    def clean(self):
        # The content type itself is held to ``limit_choices_to`` by the
        # field's own validation; the object it names must exist.
        super().clean()
        if self.content_type_id and not content_object_exists(
            self.content_type, self.object_id
        ):
            raise ValidationError(
                {
                    "object_id": ValidationError(
                        CONTENT_OBJECT_NOT_FOUND_MESSAGE,
                        code=CONTENT_OBJECT_NOT_FOUND,
                    )
                }
            )


class TaggedModel(models.Model):
    id = models.BigAutoField(primary_key=True)
    tags = GenericRelation(
        "tag.TaggedItem",
        content_type_field="content_type",
        object_id_field="object_id",
    )

    class Meta:
        abstract = True

    @property
    def tag_ids(self):
        return list(self.tags.values_list("tag_id", flat=True))

    @classmethod
    def get_tags_by_object_ids(cls, object_ids: list[int], model_cls):
        content_type = ContentType.objects.get_for_model(model_cls)
        return Tag.objects.filter(
            taggeditem__content_type=content_type,
            taggeditem__object_id__in=object_ids,
        ).distinct()

    def get_tags_for_object(self):
        return Tag.objects.filter(
            taggeditem__content_type=ContentType.objects.get_for_model(
                type(self)
            ),
            taggeditem__object_id=self.id,
        ).distinct()

    def add_tag(self, tag: TaggedItem):
        try:
            if isinstance(tag, TaggedItem):
                tag.content_object = self
                tag.save()
        except Exception as e:
            logger.error(f"Failed to add tag: {e}")

    def remove_tag(self, tag: TaggedItem):
        try:
            if isinstance(tag, TaggedItem):
                tag.delete()
        except Exception as e:
            logger.error(f"Failed to remove tag: {e}")

    def clear_tags(self):
        try:
            self.tags.all().delete()
        except Exception as e:
            logger.error(f"Failed to clear tags: {e}")
