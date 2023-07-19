import os

from django.conf import settings
from django.db import models
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from parler.models import TranslatableModel
from parler.models import TranslatedFields
from tinymce.models import HTMLField

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from tip.enum.tip_enum import TipKindEnum
from tip.validators import validate_file_extension


class Tip(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    kind = models.CharField(_("Kind"), max_length=25, choices=TipKindEnum.choices())
    icon = models.FileField(
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
        url=models.CharField(_("Url"), max_length=255, blank=True, null=True),
    )

    class Meta:
        verbose_name = _("Tip")
        verbose_name_plural = _("Tips")
        ordering = ["sort_order"]

    def __unicode__(self):
        return self.safe_translation_getter("title", any_language=True)

    def __str__(self):
        return self.safe_translation_getter("title", any_language=True)

    def get_ordering_queryset(self):
        return Tip.objects.all()

    @property
    def image_tag(self):
        icon = self.icon
        if icon:
            return mark_safe('<img src="{}" height="50"/>'.format(icon.url))
        return ""

    @property
    def main_image_absolute_url(self) -> str:
        icon: str = ""
        if self.icon and hasattr(self.icon, "url"):
            return settings.APP_BASE_URL + self.icon.url
        return icon

    @property
    def main_image_filename(self) -> str:
        if self.icon and hasattr(self.icon, "name"):
            return os.path.basename(self.icon.name)
        else:
            return ""
