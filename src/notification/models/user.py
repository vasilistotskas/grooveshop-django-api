from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel, UUIDModel
from notification.managers import NotificationUserManager


class UnseenNotificationUserManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(seen=False)


class NotificationUser(TimeStampMixinModel, UUIDModel):
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="notification_users",
        on_delete=models.CASCADE,
        verbose_name=_("User"),
    )
    notification = models.ForeignKey(
        "notification.Notification",
        related_name="notification_users",
        on_delete=models.CASCADE,
        verbose_name=_("Notification"),
    )
    seen = models.BooleanField(_("Seen"), default=False)
    seen_at = models.DateTimeField(_("Seen At"), null=True, blank=True)

    objects: NotificationUserManager = NotificationUserManager()
    unseen_objects: UnseenNotificationUserManager = (
        UnseenNotificationUserManager()
    )

    def __str__(self):
        status = gettext("Seen") if self.seen else gettext("Unseen")
        return gettext("Notification %(id)s for %(user)s: %(status)s") % {
            "id": self.notification_id,
            "user": self.user.full_name,
            "status": status,
        }

    class Meta(TypedModelMeta):
        verbose_name = _("Notification User")
        verbose_name_plural = _("Notification Users")
        ordering = ["-notification__created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "notification"],
                name="unique_notification_user",
            )
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["user"], name="not_user_user_ix"),
            BTreeIndex(fields=["notification"], name="not_user_notif_ix"),
            BTreeIndex(fields=["seen"], name="not_user_seen_ix"),
            BTreeIndex(fields=["seen_at"], name="not_user_seen_at_ix"),
        ]

    def save(self, *args, **kwargs):
        if self.seen and self.seen_at is None:
            self.seen_at = timezone.now()
        super().save(*args, **kwargs)
