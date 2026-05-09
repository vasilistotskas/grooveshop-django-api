from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class CachePurgeLog(models.Model):
    """Audit row recording who purged which cache surfaces and the result.

    The model lives in ``core`` so that the admin app and management
    commands can rely on it without introducing a new Django app.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cache_purge_logs",
        verbose_name=_("Actor"),
    )
    created_at = models.DateTimeField(_("Created at"), auto_now_add=True)
    surfaces = models.JSONField(_("Surfaces"), default=list)
    dry_run = models.BooleanField(_("Dry run"), default=False)
    total_django = models.PositiveIntegerField(_("Django keys"), default=0)
    total_nuxt = models.PositiveIntegerField(_("Nuxt keys"), default=0)
    total_blocked = models.PositiveIntegerField(_("Blocked keys"), default=0)
    detail = models.JSONField(_("Detail"), default=list)

    class Meta:
        app_label = "core"
        verbose_name = _("Cache purge log")
        verbose_name_plural = _("Cache purge logs")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["-created_at"], name="cachelog_created_idx"),
        ]

    def __str__(self) -> str:
        return f"CachePurgeLog #{self.pk} ({len(self.surfaces or [])} surfaces)"
