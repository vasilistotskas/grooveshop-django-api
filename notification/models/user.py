from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import TimeStampMixinModel
from core.models import UUIDModel


class NotificationUser(TimeStampMixinModel, UUIDModel):
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="notification",
        on_delete=models.CASCADE,
        db_index=True,
    )
    notification = models.ForeignKey(
        "notification.Notification", on_delete=models.CASCADE, db_index=True
    )
    seen = models.BooleanField(_("Seen"), default=False)

    def __unicode__(self):
        return self.notification.message

    def __str__(self):
        return self.notification.message

    class Meta:
        unique_together = (("user", "notification"),)
        verbose_name = _("Notification User")
        verbose_name_plural = _("Notification Users")
        ordering = ["-notification__created_at"]
