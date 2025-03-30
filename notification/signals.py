from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from notification.models.user import NotificationUser
from notification.tasks import send_notification_task


@receiver(post_save, sender=NotificationUser)
def handle_notification_created(
    sender, instance: NotificationUser, created, **kwargs
):
    if created:
        translations_queryset = instance.notification.translations.all().values(
            "language_code", "title", "message"
        )

        translations = {
            translation["language_code"]: {
                "title": translation["title"],
                "message": translation["message"],
            }
            for translation in translations_queryset
        }

        data = {
            "user_id": instance.user.id,
            "seen": instance.seen,
            "link": instance.notification.link,
            "kind": instance.notification.kind,
            "translations": translations,
        }

        send_notification_task.delay_on_commit(data)
