"""``TenantStatsSnapshot``: a store's headline figures, refreshed on a
schedule."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from tenant.models.tenant import Tenant


class TenantStatsSnapshot(models.Model):
    """A store's headline figures, as read inside its own schema.

    The control plane lists every store with its order count and
    revenue. Computing those per page view meant a schema switch and
    two queries per store on every render; ``refresh_stats_snapshot``
    (``src/tenant/tasks.py``) reads them inside each store's schema on a
    schedule instead, and the platform dashboard and the Tenants list
    read this row. Kept apart from ``Tenant`` because saving a ``Tenant``
    bumps its ``cache_generation`` - a refresh must not evict the
    store's caches.

    Lives in the PUBLIC schema (``tenant`` is SHARED_APPS-only).
    """

    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="stats_snapshot",
        verbose_name=_("Store"),
    )
    orders_count = models.PositiveIntegerField(_("Orders"))
    revenue = models.DecimalField(
        _("Revenue"),
        max_digits=14,
        decimal_places=2,
        help_text=_("Paid amount of every order whose payment completed."),
    )
    last_order_at = models.DateTimeField(_("Last order"), null=True)
    refreshed_at = models.DateTimeField(_("Refreshed"))

    class Meta:
        verbose_name = _("Store figures")
        verbose_name_plural = _("Store figures")

    def __str__(self) -> str:
        return f"{self.tenant} @ {self.refreshed_at:%Y-%m-%d %H:%M}"
