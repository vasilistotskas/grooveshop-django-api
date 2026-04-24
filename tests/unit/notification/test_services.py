"""Tests for ``notification.services.create_user_notification``.

We pin the helper's contract independently of the signal chain so a
refactor that reshuffles how Notification rows fan out (e.g. switching
to ``bulk_create`` for one-notification-per-user tasks) doesn't silently
drop a language or skip the translation save. The signal chain itself
(post_save on NotificationUser → send_notification_task) is covered
separately in the consumer/signal tests.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings

from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationPriorityEnum,
    NotificationTypeEnum,
)
from notification.models.notification import Notification
from notification.models.user import NotificationUser
from notification.services import create_user_notification
from user.factories.account import UserAccountFactory


class CreateUserNotificationTestCase(TestCase):
    def setUp(self) -> None:
        self.user = UserAccountFactory()

    def test_creates_notification_user_and_returns_row(self) -> None:
        notification_user = create_user_notification(
            self.user,
            translations={
                "en": {"title": "Hello", "message": "World"},
            },
        )
        self.assertIsInstance(notification_user, NotificationUser)
        self.assertEqual(notification_user.user_id, self.user.pk)
        self.assertEqual(
            notification_user.notification.kind, NotificationKindEnum.INFO
        )
        self.assertEqual(
            notification_user.notification.category,
            NotificationCategoryEnum.SYSTEM,
        )
        self.assertEqual(
            notification_user.notification.priority,
            NotificationPriorityEnum.NORMAL,
        )

    def test_applies_kind_category_priority_link(self) -> None:
        result = create_user_notification(
            self.user,
            kind=NotificationKindEnum.ERROR,
            category=NotificationCategoryEnum.PAYMENT,
            priority=NotificationPriorityEnum.HIGH,
            notification_type=NotificationTypeEnum.PAYMENT_FAILED,
            link="/account/orders/42",
            translations={
                "en": {"title": "T", "message": "M"},
            },
        )
        n = result.notification
        self.assertEqual(n.kind, NotificationKindEnum.ERROR)
        self.assertEqual(n.category, NotificationCategoryEnum.PAYMENT)
        self.assertEqual(n.priority, NotificationPriorityEnum.HIGH)
        self.assertEqual(
            n.notification_type, NotificationTypeEnum.PAYMENT_FAILED
        )
        self.assertEqual(n.link, "/account/orders/42")

    def test_skips_unsupported_languages_silently(self) -> None:
        """Languages outside PARLER_LANGUAGES must drop without raising.

        Lets callers ship an ``en``/``el``/``de`` dict even when the
        environment is configured for a subset — the alternative
        (raising) would make tests brittle against settings drift.
        """
        with patch(
            "notification.services.supported_notification_languages",
            return_value=["en"],
        ):
            notification_user = create_user_notification(
                self.user,
                translations={
                    "en": {"title": "Hi", "message": "Hello"},
                    "xx": {"title": "BAD", "message": "BAD"},
                },
            )
        translations = notification_user.notification.translations.values_list(
            "language_code", flat=True
        )
        self.assertEqual(list(translations), ["en"])

    def test_skips_languages_with_empty_copy(self) -> None:
        """A language entry with blank title AND message is dropped so
        parler's fallback chain kicks in instead of serving a blank UI."""
        with patch(
            "notification.services.supported_notification_languages",
            return_value=["en", "el"],
        ):
            notification_user = create_user_notification(
                self.user,
                translations={
                    "en": {"title": "Hi", "message": "Hello"},
                    "el": {"title": "", "message": ""},
                },
            )
        translations = notification_user.notification.translations.values_list(
            "language_code", flat=True
        )
        self.assertEqual(list(translations), ["en"])

    def test_partial_translation_save_is_rolled_back(self) -> None:
        """The decorator @transaction.atomic wrapping
        create_user_notification must roll back the Notification row
        when a translation save crashes mid-loop."""
        initial_count = Notification.objects.count()
        with patch(
            "notification.services.supported_notification_languages",
            return_value=["en", "el"],
        ):
            with patch.object(
                Notification, "save", side_effect=[None, RuntimeError("boom")]
            ):
                with self.assertRaises(RuntimeError):
                    create_user_notification(
                        self.user,
                        translations={
                            "en": {"title": "A", "message": "B"},
                            "el": {"title": "Γ", "message": "Δ"},
                        },
                    )
        self.assertEqual(Notification.objects.count(), initial_count)

    @override_settings(
        PARLER_LANGUAGES={
            1: ({"code": "en"}, {"code": "el"}, {"code": "de"}),
        }
    )
    def test_all_supplied_languages_persisted(self) -> None:
        notification_user = create_user_notification(
            self.user,
            translations={
                "en": {"title": "English", "message": "Hello"},
                "el": {"title": "Ελληνικά", "message": "Γειά"},
                "de": {"title": "Deutsch", "message": "Hallo"},
            },
        )
        languages = sorted(
            notification_user.notification.translations.values_list(
                "language_code", flat=True
            )
        )
        self.assertEqual(languages, ["de", "el", "en"])
