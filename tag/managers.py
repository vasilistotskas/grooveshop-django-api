from django.contrib.contenttypes.models import ContentType
from django.db import models
from parler.managers import TranslatableManager


class TagManager(TranslatableManager):
    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related()
            .prefetch_related("translations")
        )

    def active(self):
        return self.get_queryset().filter(active=True)

    def inactive(self):
        return self.get_queryset().filter(active=False)


class TaggedItemManager(models.Manager):
    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("tag", "content_type")
            .prefetch_related("tag__translations")
        )

    def active_tags(self):
        return self.get_queryset().filter(tag__active=True)

    def get_tags_for(self, obj_type, obj_id):
        content_type = ContentType.objects.get_for_model(obj_type)

        return self.get_queryset().filter(
            content_type=content_type, object_id=obj_id
        )
