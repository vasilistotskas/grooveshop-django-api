from typing import override

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.managers import TranslatableManager
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class ActiveBlogTagManager(TranslatableManager):
    @override
    def get_queryset(self):
        return super().get_queryset().filter(active=True)


class Tag(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    active = models.BooleanField(_("Active"), default=True)
    translations = TranslatedFields(
        label=models.CharField(
            _("Label"),
            max_length=255,
            blank=True,
            null=True,
        )
    )

    active_tags = ActiveBlogTagManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(fields=["active"]),
        ]

    def __str__(self):
        tag_label = self.safe_translation_getter("label", any_language=True) or "Unnamed Label"
        return f"{tag_label} ({'Active' if self.active else 'Inactive'})"

    @override
    def get_ordering_queryset(self):
        return Tag.objects.all()
