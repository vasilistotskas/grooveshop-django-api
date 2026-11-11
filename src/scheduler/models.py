"""What the database knows about a periodic task: its state, never its schedule.

``settings.CELERY_BEAT_SCHEDULE`` declares what runs and when. A row holds
what cannot live in code: whether the entry is switched on, and when it last
ran, which is what lets beat send a tick it missed while restarting.
"""

from datetime import datetime

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class PeriodicTaskQuerySet(models.QuerySet):
    def switch_on(self) -> int:
        """Stamp ``resumed_at``, so the slots missed while off are not sent."""
        return self.filter(enabled=False).update(
            enabled=True, resumed_at=timezone.now()
        )

    def switch_off(self) -> int:
        return self.filter(enabled=True).update(enabled=False)


class PeriodicTask(models.Model):
    name = models.CharField(_("Name"), max_length=200, unique=True)
    enabled = models.BooleanField(_("Enabled"), default=True, db_default=True)
    resumed_at = models.DateTimeField(
        _("Switched on at"), null=True, blank=True
    )
    last_run_at = models.DateTimeField(_("Last run"), null=True, blank=True)
    total_run_count = models.PositiveBigIntegerField(
        _("Runs"), default=0, db_default=0
    )

    objects = PeriodicTaskQuerySet.as_manager()

    class Meta:
        ordering = ("name",)
        verbose_name = _("Periodic Task")
        verbose_name_plural = _("Periodic Tasks")

    def __str__(self) -> str:
        return self.name

    @property
    def counted_from(self) -> datetime | None:
        """The moment after which a crontab slot counts as due."""
        return max(
            filter(None, (self.last_run_at, self.resumed_at)), default=None
        )
