import logging

from django.contrib.contenttypes.fields import (
    GenericForeignKey,
    GenericRelation,
)
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel, UUIDModel
from tag.managers import TaggedItemManager
from tag.models.tag import Tag

logger = logging.getLogger(__name__)


class TaggedItem(TimeStampMixinModel, UUIDModel):
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
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
