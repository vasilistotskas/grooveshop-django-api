from django.db import models
from django.utils import timezone as tz
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from core.models import UUIDModel


class UnseenNotificationUserManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(seen=False)


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
    seen_at = models.DateTimeField(_("Seen At"), null=True, blank=True)

    def __unicode__(self):
        status = "seen" if self.seen else "unseen"
        return (
            f"Notification {self.notification.id} for {self.user.full_name}: {status}"
        )

    def __str__(self):
        status = "seen" if self.seen else "unseen"
        return (
            f"Notification {self.notification.id} for {self.user.full_name}: {status}"
        )

    class Meta(TypedModelMeta):
        verbose_name = _("Notification User")
        verbose_name_plural = _("Notification Users")
        ordering = ["-notification__created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "notification"], name="unique_notification_user"
            )
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
        ]

    def save(self, *args, **kwargs):
        if self.seen and self.seen_at is None:
            self.seen_at = tz.now()
        super().save(*args, **kwargs)
