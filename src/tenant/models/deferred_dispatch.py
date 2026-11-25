"""``DeferredDispatch``: a task kept after its publish failed."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class DeferredDispatch(models.Model):
    """A task whose publish failed after the change that queued it had
    committed (the broker was unreachable), kept so it is not lost.

    ``tenant.celery.dispatch_on_commit`` writes a row when the publish
    raises a network error; ``tenant.tasks.redispatch_deferred_tasks``
    publishes it again, with a backoff, and deletes it once it is out. The
    payload is exactly what ``apply_async`` was given, schema header
    included. A row older than ``DEFERRED_DISPATCH_RETENTION_DAYS`` is
    deleted with an ERROR log: the work it stood for was not done.

    Lives in ``public`` whatever the store: the sweep that publishes the
    rows runs there.
    """

    task_name = models.CharField(_("Task"), max_length=255)
    args = models.JSONField(_("Arguments"), default=list)
    kwargs = models.JSONField(_("Keyword arguments"), default=dict)
    headers = models.JSONField(_("Headers"), default=dict)
    options = models.JSONField(_("Options"), default=dict)
    schema_name = models.CharField(
        _("Store"), max_length=63, blank=True, default=""
    )
    attempts = models.PositiveSmallIntegerField(_("Attempts"), default=0)
    next_attempt_at = models.DateTimeField(_("Next attempt at"))
    last_error = models.CharField(
        _("Last error"), max_length=255, blank=True, default=""
    )
    created_at = models.DateTimeField(_("Created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("Deferred dispatch")
        verbose_name_plural = _("Deferred dispatches")
        ordering = ["next_attempt_at", "id"]
        indexes = [
            models.Index(
                fields=["next_attempt_at"], name="deferred_dispatch_due_ix"
            )
        ]

    def __str__(self) -> str:
        return f"{self.task_name} for {self.schema_name or '-'}"
