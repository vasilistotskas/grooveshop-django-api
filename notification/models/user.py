from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

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
        return f"{self.user} {self.notification}"

    def __str__(self):
        return f"{self.user} {self.notification}"

    class Meta(TypedModelMeta):
        verbose_name = _("Notification User")
        verbose_name_plural = _("Notification Users")
        ordering = ["-notification__created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "notification"], name="unique_notification_user"
            )
        ]
