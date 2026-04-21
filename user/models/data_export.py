from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import TimeStampMixinModel, UUIDModel


class UserDataExport(UUIDModel, TimeStampMixinModel):
    """Tracks async generation of a user's GDPR data-export bundle.

    The actual payload (a single JSON file) is written to private media
    storage by ``export_user_data_task``. The ``token`` column backs a
    one-off download link that the email sends the user; it is not the
    same as the user's auth token and carries no API access.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        READY = "ready", _("Ready")
        FAILED = "failed", _("Failed")
        EXPIRED = "expired", _("Expired")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="data_exports",
        verbose_name=_("User"),
    )
    status = models.CharField(
        _("Status"),
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    file_path = models.CharField(
        _("File Path"),
        max_length=512,
        blank=True,
        default="",
        help_text=_("Path under MEDIA_ROOT/_private/exports/."),
    )
    file_size = models.PositiveIntegerField(
        _("File Size (bytes)"),
        null=True,
        blank=True,
    )
    token = models.CharField(
        _("Download Token"),
        max_length=64,
        unique=True,
        db_index=True,
        help_text=_("Random token carried by the download URL."),
    )
    expires_at = models.DateTimeField(
        _("Expires At"),
        null=True,
        blank=True,
        help_text=_("When the download link stops working."),
    )
    error_message = models.TextField(
        _("Error Message"),
        blank=True,
        default="",
    )

    class Meta:
        verbose_name = _("User Data Export")
        verbose_name_plural = _("User Data Exports")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            models.Index(fields=["user", "status"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"Export#{self.pk} user={self.user_id} status={self.status}"

    @property
    def is_ready(self) -> bool:
        return self.status == self.Status.READY and bool(self.file_path)
