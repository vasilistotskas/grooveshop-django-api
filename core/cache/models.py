from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class CachePurgeLogQuerySet(models.QuerySet):
    def visible_here(self):
        """Rows the CURRENT schema is allowed to see.

        The control plane sees everything. A store sees only what it caused:
        this table exists only in ``public``, so an unscoped read on a tenant
        host falls through the search path and returns every store's activity.

        Rows with no ``schema_name`` predate that field. Which store caused
        them is not recoverable, and guessing would put another store's
        activity on a merchant's page, so they stay on the control plane.
        """
        from django.db import connection
        from django_tenants.utils import get_public_schema_name

        schema = getattr(connection, "schema_name", get_public_schema_name())
        if schema == get_public_schema_name():
            return self
        return self.filter(schema_name=schema)


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
    # Denormalised on purpose. ``actor`` is a CROSS-SCHEMA foreign key: this
    # table lives only in ``public`` (``core`` is SHARED_APPS only), so a read
    # from a tenant schema resolves the id against THAT schema's user table and
    # names whoever shares the primary key — measured in production as a
    # customer being credited with a platform operator's purge. An audit row
    # that names the wrong person is worse than one that names nobody, so the
    # display never depends on resolving the FK.
    actor_email = models.EmailField(
        _("Actor email"), blank=True, default="", max_length=254
    )
    # Which store issued the purge, captured from the active schema at write
    # time. Without it the panel cannot be scoped and every merchant sees every
    # other merchant's activity. Empty means "recorded before this field
    # existed" and is shown only on the control plane, never to a store.
    schema_name = models.CharField(
        _("Schema"), max_length=63, blank=True, default="", db_index=True
    )
    created_at = models.DateTimeField(_("Created at"), auto_now_add=True)
    surfaces = models.JSONField(_("Surfaces"), default=list)
    dry_run = models.BooleanField(_("Dry run"), default=False)
    total_django = models.PositiveIntegerField(_("Django keys"), default=0)
    total_nuxt = models.PositiveIntegerField(_("Nuxt keys"), default=0)
    total_blocked = models.PositiveIntegerField(_("Blocked keys"), default=0)
    detail = models.JSONField(_("Detail"), default=list)

    objects = CachePurgeLogQuerySet.as_manager()

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
