from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from notification.models.user import NotificationUser
from notification.tasks import send_notification_task


@receiver(post_save, sender=NotificationUser)
def handle_notification_created(sender, instance: NotificationUser, created, **kwargs):
    if created:
        translations_queryset = instance.notification.translations.all().values("language_code", "title", "message")

        translations = {
            translation["language_code"]: {"title": translation["title"], "message": translation["message"]}
            for translation in translations_queryset
        }

        send_notification_task.delay_on_commit(
            instance.user.id,
            instance.seen,
            instance.notification.link,
            instance.notification.kind,
            translations,
        )
