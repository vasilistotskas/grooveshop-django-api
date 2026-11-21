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

import json

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import IntegrityError, transaction

from core.utils.tenant_urls import validate_storefront_path
from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationPriorityEnum,
)
from notification.models.notification import Notification
from notification.models.user import NotificationUser
from tenant.celery import dispatch_key
from tenant.customer_accounts import refuses_customer

_DISPATCH_NAMESPACE = "notification"


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
) -> NotificationUser | None:
    """Create a notification for a single user with multi-locale content.

    Returns ``None`` and writes nothing for a shopper of a store with
    customer accounts off (``tenant.customer_accounts``): no sign-in that
    could show it is accepted there, and a notification kept for later
    would greet the shopper as a backlog if the store turned accounts
    back on. Every in-app notification is written here, so this is the
    one place that holds.

    ``translations`` is a mapping ``{language_code: {"title": ..., "message": ...}}``.
    Locales present in ``PARLER_LANGUAGES[SITE_ID]`` but absent from the
    caller's dict fall back to the ``en`` entry, then ``el`` — silent blank
    rows are never written.
    Extra keys in ``translations`` that are not in ``PARLER_LANGUAGES`` are
    ignored so callers can pass a larger dict without environment coupling.

    Wrapped in an atomic block so a partial translation-save failure
    cannot leak a half-populated Notification row.

    ``link`` is a locale-neutral storefront path (build it with
    ``core.utils.tenant_urls.storefront_path``). It is validated here
    because ``objects.create`` never runs the field's validators, and
    this is the one write path every task goes through.

    Inside a task the notification is written once per dispatch: its
    ``dispatch_key`` is made of the task id, the user and everything the
    notification says (``tenant.celery.dispatch_key``), and is unique, so
    one task can still write two different notifications to one user (two
    likes of a comment). Another copy of the task (a retry, a message
    delivered again, the sweep's copy of a publish) gets back the row the
    first one wrote, and the live push that follows a new row is not
    queued again. A copy that runs at the same moment waits on the unique
    index for the first to commit.
    """
    if refuses_customer(user):
        return None
    if link:
        validate_storefront_path(link)
    key = dispatch_key(
        _DISPATCH_NAMESPACE,
        user.pk,
        kind,
        category,
        priority,
        notification_type,
        link,
        json.dumps(translations, sort_keys=True, ensure_ascii=False),
    )
    try:
        with transaction.atomic():
            notification = Notification.objects.create(
                kind=kind,
                category=category,
                priority=priority,
                notification_type=notification_type,
                link=link,
                dispatch_key=key,
            )
    except IntegrityError:
        if key is None:
            raise
        return NotificationUser.objects.get(
            user=user, notification__dispatch_key=key
        )

    supported = supported_notification_languages()
    # Fallback chain used when a locale has no entry in the caller's
    # ``translations`` dict.  "en" is tried first; if absent, "el" is used.
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
