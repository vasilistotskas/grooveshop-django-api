from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.logging import LogInfo
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from tag.models.tag import Tag


class TaggedItemManager(models.Manager):
    def get_tags_for(self, obj_type, obj_id):
        content_type = ContentType.objects.get_for_model(obj_type)

        return TaggedItem.objects.select_related("tag").filter(content_type=content_type, object_id=obj_id)


class TaggedItem(TimeStampMixinModel, UUIDModel):
    objects = TaggedItemManager()
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey()

    class Meta(TypedModelMeta):
        verbose_name = _("Tagged Item")
        verbose_name_plural = _("Tagged Items")
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]


class TaggedModel(models.Model):
    tags = GenericRelation("tag.TaggedItem", content_type_field="content_type", object_id_field="object_id")

    class Meta:
        abstract = True

    @property
    def tag_ids(self) -> list[int]:
        return list(self.tags.values_list("tag_id", flat=True))

    @classmethod
    def get_tags_by_object_ids(cls, object_ids: list[int], model_cls) -> QuerySet[Tag]:
        content_type = ContentType.objects.get_for_model(model_cls)
        return Tag.objects.filter(
            taggeditem__content_type=content_type, taggeditem__object_id__in=object_ids
        ).distinct()

    def get_tags_for_object(self) -> QuerySet[Tag]:
        return Tag.objects.filter(
            taggeditem__content_type=ContentType.objects.get_for_model(type(self)), taggeditem__object_id=self.id
        ).distinct()

    def add_tag(self, tag: TaggedItem) -> None:
        try:
            if isinstance(tag, TaggedItem):
                tag.content_object = self
                tag.save()
        except Exception as e:
            LogInfo.error(f"Failed to add tag: {e}")

    def remove_tag(self, tag: TaggedItem) -> None:
        try:
            if isinstance(tag, TaggedItem):
                tag.delete()
        except Exception as e:
            LogInfo.error(f"Failed to remove tag: {e}")

    def clear_tags(self) -> None:
        try:
            self.tags.all().delete()
        except Exception as e:
            LogInfo.error(f"Failed to clear tags: {e}")
