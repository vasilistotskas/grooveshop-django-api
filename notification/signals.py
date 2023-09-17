from django.db.models.signals import post_save
from django.dispatch import receiver

from notification.models.user import NotificationUser
from notification.tasks import send_notification_task


@receiver(post_save, sender=NotificationUser)
def handle_notification_created(sender, instance: NotificationUser, **kwargs):
    translations = instance.notification.translations.all().values(
        "language_code", "message"
    )

    send_notification_task.delay(
        user=int(instance.user.id),
        seen=instance.seen,
        link=instance.notification.link,
        kind=instance.notification.kind,
        translations=list(translations),
    )
