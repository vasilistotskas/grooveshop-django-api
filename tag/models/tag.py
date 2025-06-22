from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from tag.managers import TagManager


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

    objects: TagManager = TagManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(fields=["active"], name="tag_active_ix"),
            BTreeIndex(fields=["id"], name="tag_id_ix"),
        ]

    def __str__(self):
        tag_label = (
            self.safe_translation_getter("label", any_language=True)
            or "Unnamed Label"
        )
        return f"{tag_label} ({'Active' if self.active else 'Inactive'})"

    def get_ordering_queryset(self):
        return Tag.objects.all()
