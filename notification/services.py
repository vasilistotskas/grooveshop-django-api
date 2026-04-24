"""Service-layer helpers for creating notifications.

Keeps translation/fan-out boilerplate out of individual Celery tasks —
callers describe *what* a notification says in each locale and the
service handles the Notification + translations + NotificationUser
choreography. The NotificationUser create triggers the WebSocket
dispatch chain via the ``notification.signals.handle_notification_created``
post-save handler, so the caller does not need to worry about real-time
delivery.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationPriorityEnum,
)
from notification.models.notification import Notification
from notification.models.user import NotificationUser


def supported_notification_languages() -> list[str]:
    """Return the language codes configured for parler.

    Exposed at module level so callers (Celery tasks, tests) can build
    their ``translations`` dicts against the same source of truth the
    service uses for filtering.
    """
    return [
        lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
    ]


@transaction.atomic
def create_user_notification(
    user: AbstractBaseUser,
    *,
    translations: dict[str, dict[str, str]],
    kind: str = NotificationKindEnum.INFO,
    category: str = NotificationCategoryEnum.SYSTEM,
    priority: str = NotificationPriorityEnum.NORMAL,
    notification_type: str = "",
    link: str = "",
) -> NotificationUser:
    """Create a notification for a single user with multi-locale content.

    ``translations`` is a mapping ``{language_code: {"title": ..., "message": ...}}``.
    Language codes outside ``PARLER_LANGUAGES[SITE_ID]`` are silently
    dropped so callers can ship an ``en``/``el``/``de`` dict even in
    environments where only a subset is configured — the alternative
    (raising) would make tests brittle against settings drift.

    Wrapped in an atomic block so a partial translation-save failure
    cannot leak a half-populated Notification row.
    """
    notification = Notification.objects.create(
        kind=kind,
        category=category,
        priority=priority,
        notification_type=notification_type,
        link=link,
    )

    supported = set(supported_notification_languages())
    for language, fields in translations.items():
        if language not in supported:
            continue
        title = fields.get("title", "")
        message = fields.get("message", "")
        # Skip languages where the caller has no copy for the event —
        # better to fall back to parler's own fallback chain than to
        # save an empty ``title`` that the UI would render blank.
        if not title and not message:
            continue
        notification.set_current_language(language)
        notification.title = title
        notification.message = message
        notification.save()

    return NotificationUser.objects.create(user=user, notification=notification)
