from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import TimeStampMixinModel
from core.models import UUIDModel
from notification.enum import NotificationKindEnum


class Notification(TranslatableModel, TimeStampMixinModel, UUIDModel):
    link = models.URLField(_("Link"), blank=True, null=True)
    kind = models.CharField(
        _("Kind"),
        max_length=250,
        choices=NotificationKindEnum.choices,
        default=NotificationKindEnum.INFO,
    )
    translations = TranslatedFields(
        message=models.TextField(_("Message")),
    )

    def __unicode__(self):
        return self.safe_translation_getter("message", any_language=True) or ""

    def __str__(self):
        return self.safe_translation_getter("message", any_language=True) or ""

    class Meta(TypedModelMeta):
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        ordering = ["-created_at"]
