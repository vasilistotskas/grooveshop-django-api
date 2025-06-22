from datetime import timedelta

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.db.models.functions import Now
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import TimeStampMixinModel, UUIDModel
from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationPriorityEnum,
)
from notification.managers import NotificationManager

EXPIRATION_DAYS = 6 * 30


class Notification(TranslatableModel, TimeStampMixinModel, UUIDModel):
    link = models.URLField(_("Link"), blank=True, default="")
    kind = models.CharField(
        _("Kind"),
        max_length=250,
        choices=NotificationKindEnum,
        default=NotificationKindEnum.INFO,
    )
    category = models.CharField(
        _("Category"),
        max_length=50,
        choices=NotificationCategoryEnum,
        default=NotificationCategoryEnum.SYSTEM,
    )
    priority = models.CharField(
        _("Priority"),
        max_length=20,
        choices=NotificationPriorityEnum,
        default=NotificationPriorityEnum.NORMAL,
    )
    notification_type = models.CharField(
        _("Notification Type"),
        max_length=100,
        blank=True,
        help_text=_("Specific type of notification for categorization"),
    )
    expiry_date = models.DateTimeField(
        _("Expiry Date"),
        null=True,
        blank=True,
        db_default=Now() + timedelta(days=EXPIRATION_DAYS),
    )
    translations = TranslatedFields(
        title=models.CharField(_("Title"), max_length=250),
        message=models.TextField(_("Message")),
    )

    objects: NotificationManager = NotificationManager()

    def __str__(self):
        message_snippet = (
            self.safe_translation_getter("title", any_language=True)[:50]
            + "..."
        )
        return f"{self.get_kind_display()}: {message_snippet}"

    class Meta(TypedModelMeta):
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["kind"], name="notification_kind_ix"),
            BTreeIndex(fields=["category"], name="notification_category_ix"),
            BTreeIndex(fields=["priority"], name="notification_priority_ix"),
            BTreeIndex(
                fields=["notification_type"], name="notification_type_ix"
            ),
            BTreeIndex(
                fields=["expiry_date"], name="notification_expiry_date_ix"
            ),
            BTreeIndex(
                fields=["category", "priority"], name="notification_cat_pri_ix"
            ),
        ]

    def is_expired(self):
        return self.expiry_date and timezone.now() > self.expiry_date
