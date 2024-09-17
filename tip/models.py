import os
from typing import override

from django.db import models
from django.templatetags.static import static
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel
from parler.models import TranslatedFields
from tinymce.models import HTMLField

from core.fields.image import ImageAndSvgField
from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from tip.enum.tip_enum import TipKindEnum
from tip.validators import validate_file_extension


class Tip(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    kind = models.CharField(_("Kind"), max_length=25, choices=TipKindEnum)
    icon = ImageAndSvgField(
        _("Icon"),
        upload_to="uploads/tip/",
        validators=[validate_file_extension],
        null=True,
        blank=True,
    )
    active = models.BooleanField(_("Active"), default=True)
    translations = TranslatedFields(
        title=models.CharField(_("Title"), max_length=200, blank=True, null=True),
        content=HTMLField(_("Content"), blank=True, null=True),
        url=models.URLField(_("Url"), max_length=255, blank=True, null=True),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Tip")
        verbose_name_plural = _("Tips")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
        ]

    def __unicode__(self):
        return f"{self.get_kind_display()}: {self.safe_translation_getter('title', any_language=True)}"

    def __str__(self):
        return f"{self.get_kind_display()}: {self.safe_translation_getter('title', any_language=True)}"

    @override
    def get_ordering_queryset(self):
        return Tip.objects.all()

    @property
    def image_tag(self):
        no_img_url = static("images/no_photo.jpg")
        no_img_markup = mark_safe(f'<img src="{no_img_url}" width="100" height="100" />')
        if self.icon:
            return mark_safe('<img src="{}" width="100" height="100" />'.format(self.icon.url))
        return no_img_markup

    @property
    def main_image_path(self) -> str:
        if self.icon and hasattr(self.icon, "name"):
            return f"media/uploads/tip/{os.path.basename(self.icon.name)}"
        return ""
