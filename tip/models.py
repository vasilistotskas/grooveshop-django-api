import os

from django.conf import settings
from django.db import models
from django.utils.safestring import mark_safe

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from tip.enum.tip_enum import TipKindEnum
from tip.validators import validate_file_extension


class Tip(TimeStampMixinModel, SortableModel, UUIDModel):
    title = models.CharField(max_length=200)
    content = models.TextField(max_length=1000)
    kind = models.CharField(max_length=10, choices=TipKindEnum.choices())
    icon = models.FileField(
        upload_to="uploads/tip/",
        validators=[validate_file_extension],
        null=True,
        blank=True,
    )
    url = models.CharField(max_length=200)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

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
