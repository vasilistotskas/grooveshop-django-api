from __future__ import annotations

from django.core.cache import cache
from django.db.models.signals import post_save
from django.dispatch import receiver

from notification.models.user import NotificationUser
from notification.tasks import send_notification_task

# Short TTL cache for serialized notification translations. When a
# Notification fans out to N users (e.g. a system-wide announcement),
# the signal fires N times — without this cache each dispatch would
# issue its own SELECT on notification_translations.
_TRANSLATIONS_CACHE_TTL = 60
_TRANSLATIONS_CACHE_PREFIX = "notification:translations:"


def _get_translations(notification) -> dict:
    key = f"{_TRANSLATIONS_CACHE_PREFIX}{notification.pk}"
    translations = cache.get(key)
    if translations is not None:
        return translations

    translations = {
        row["language_code"]: {
            "title": row["title"],
            "message": row["message"],
        }
        for row in notification.translations.all().values(
            "language_code", "title", "message"
        )
    }
    cache.set(key, translations, _TRANSLATIONS_CACHE_TTL)
    return translations


@receiver(
    post_save,
    sender=NotificationUser,
    dispatch_uid="notification.handle_notification_created",
)
def handle_notification_created(
    sender, instance: NotificationUser, created, **kwargs
):
    if not created:
        return

    data = {
        "user_id": instance.user_id,
        "id": instance.id,
        "seen": instance.seen,
        "link": instance.notification.link,
        "kind": instance.notification.kind,
        "category": instance.notification.category,
        "priority": instance.notification.priority,
        "notification_type": instance.notification.notification_type,
        "translations": _get_translations(instance.notification),
    }

    send_notification_task.delay_on_commit(data)
