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
    Locales present in ``PARLER_LANGUAGES[SITE_ID]`` but absent from the
    caller's dict fall back to the ``en`` entry, then ``el``.  This means
    German-locale users see English copy until proper ``de`` translations
    are added to each task — silent blank rows are never written.
    Extra keys in ``translations`` that are not in ``PARLER_LANGUAGES`` are
    ignored so callers can pass a larger dict without environment coupling.

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

    supported = supported_notification_languages()
    # Fallback chain used when a locale has no entry in the caller's
    # ``translations`` dict (e.g. "de" is not yet translated).  "en" is
    # tried first; if absent, "el" is used.  de-locale users therefore
    # see English copy until proper German translations are added.
    _fallback_keys = ["en", "el"]

    for code in supported:
        entry = translations.get(code) or next(
            (translations[k] for k in _fallback_keys if k in translations),
            None,
        )
        if entry is None:
            continue
        title = entry.get("title", "")
        message = entry.get("message", "")
        # Skip if the resolved entry is also empty — nothing useful to write.
        if not title and not message:
            continue
        notification.set_current_language(code)
        notification.title = title
        notification.message = message
        notification.save()

    return NotificationUser.objects.create(user=user, notification=notification)
