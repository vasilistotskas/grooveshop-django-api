"""The control plane's dashboard widgets.

Everything here reads the PUBLIC schema only: per-store figures come
from ``TenantStatsSnapshot`` rows that ``refresh_stats_snapshot`` writes
from inside each store's schema, so rendering this page never switches
schema. It used to, twice per store per page view.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.apps import apps
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django_tenants.utils import get_public_schema_name
from unfold.components import register_component

from admin.dashboard.base import DashboardWidget
from admin.displays import format_dt, link_cell, money
from tenant.admin_labels import plan_badge, status_badge


def _stores():
    Tenant = apps.get_model("tenant", "Tenant")
    return (
        Tenant.objects.exclude(schema_name=get_public_schema_name())
        .select_related("stats_snapshot")
        .prefetch_related("domains")
        .order_by("name")
    )


@register_component
class PlatformAlerts(DashboardWidget):
    """What the platform's operators must fix: the platform's own mail
    that failed this week, and a store whose mail cannot be sent through
    the selected transport (``emails.setup``)."""

    def _failing_platform_emails(self) -> list[dict[str, Any]]:
        """The platform's own emails (billing notices, the staff password
        reset, the health and backup alerts) that did not arrive in the last
        7 days: read from the ``public`` log, which this page is in."""
        from emails import figures
        from emails.models import EmailLog

        failing = EmailLog.objects.filter(figures.failing_since()).count()
        if not failing:
            return []
        return [
            {
                "variant": "danger",
                "icon": "mail",
                "title": _("The platform's emails are failing"),
                "text": ngettext(
                    "%(count)s email of the platform did not arrive in the "
                    "last 7 days: a billing notice, a password reset or an "
                    "alert to the operators.",
                    "%(count)s emails of the platform did not arrive in the "
                    "last 7 days: billing notices, password resets or "
                    "alerts to the operators.",
                    failing,
                )
                % {"count": failing},
                "items": [],
                "link": {
                    "href": self.changelist_url("emails.EmailLog", "failing=7"),
                    "text": _("Open the email log"),
                },
            }
        ]

    def shape(self, data: None) -> dict[str, Any]:
        from emails import setup

        alerts = self._failing_platform_emails()
        stores = setup.stores_lacking_resend_key()
        if not stores:
            return {"alerts": alerts}
        return {
            "alerts": [
                *alerts,
                {
                    "variant": "danger",
                    "icon": "mail",
                    "title": _(
                        "Emails cannot be sent until a Resend key is saved"
                    ),
                    "text": _(
                        "Email goes through Resend and these stores send from "
                        "their own verified domain, which only their own "
                        "Resend key can sign for. Open each store, tab "
                        "Email & Security:"
                    ),
                    "items": [
                        link_cell(
                            self.change_url("tenant.Tenant", store.pk),
                            _(
                                "Emails can't be sent from %(domain)s until its "
                                "Resend key is saved"
                            )
                            % {"domain": setup.sender_domain(store)},
                        )
                        for store in stores
                    ],
                    "link": None,
                },
            ]
        }


@register_component
class PlatformEstateCards(DashboardWidget):
    """Stores, live, suspended, and background-job health."""

    def shape(self, data: None) -> dict[str, Any]:
        stores = list(_stores())
        live = sum(
            1
            for store in stores
            if store.is_active and store.suspended_at is None
        )
        suspended = sum(1 for store in stores if store.suspended_at is not None)

        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
        TaskResult = apps.get_model("django_celery_results", "TaskResult")
        scheduled = PeriodicTask.objects.filter(enabled=True).count()
        # Only failures are stored (CELERY_TASK_IGNORE_RESULT), in one
        # public-schema table, so this covers every store.
        failed = TaskResult.objects.filter(
            status="FAILURE",
            date_done__gte=timezone.now() - timedelta(hours=24),
        ).count()

        return {
            # No grid-cols-1 (a grid is one column without it): Unfold's
            # stylesheet loads after ours, so its .grid-cols-1 would
            # override sm:grid-cols-2, which only ours carries.
            "class": "grid gap-4 sm:grid-cols-2 xl:grid-cols-4",
            "kpis": [
                {
                    "icon": "storefront",
                    "tone": "primary",
                    "value": len(stores),
                    "label": _("Stores"),
                    "href": self.changelist_url("tenant.Tenant"),
                },
                {
                    "icon": "check_circle",
                    "tone": "success",
                    "value": live,
                    "label": _("Live"),
                },
                {
                    "icon": "pause_circle",
                    "tone": "danger" if suspended else "info",
                    "value": suspended,
                    "label": _("Suspended"),
                    "href": self.changelist_url(
                        "tenant.Tenant", "suspended_at__isnull=False"
                    )
                    if suspended
                    else None,
                },
                {
                    "icon": "error" if failed else "task_alt",
                    "tone": "danger" if failed else "success",
                    "value": failed,
                    "label": _("Failed background tasks (24h)"),
                    "footer": ngettext(
                        "%(count)s scheduled task enabled",
                        "%(count)s scheduled tasks enabled",
                        scheduled,
                    )
                    % {"count": scheduled},
                    "href": self.changelist_url(
                        "django_celery_results.TaskResult",
                        "status__exact=FAILURE",
                    )
                    if failed
                    else None,
                },
            ],
        }


@register_component
class PlatformStoresTable(DashboardWidget):
    def shape(self, data: None) -> dict[str, Any]:
        rows = []
        for store in _stores():
            primary = next(
                (domain for domain in store.domains.all() if domain.is_primary),
                None,
            )
            snapshot = getattr(store, "stats_snapshot", None)
            rows.append(
                [
                    link_cell(
                        self.change_url("tenant.Tenant", store.pk),
                        store.store_name or store.name,
                    ),
                    primary.domain if primary else "—",
                    plan_badge(store),
                    status_badge(store),
                    # "—" until the first refresh: a store not yet read
                    # is not a store with no orders.
                    {
                        "content": "—"
                        if snapshot is None
                        else formats.number_format(
                            snapshot.orders_count,
                            use_l10n=True,
                            force_grouping=True,
                        ),
                        "class": "text-right",
                    },
                    {
                        "content": "—"
                        if snapshot is None
                        else money(snapshot.revenue),
                        "class": "text-right",
                    },
                    "—"
                    if snapshot is None or snapshot.last_order_at is None
                    else format_dt(
                        snapshot.last_order_at, fmt="SHORT_DATE_FORMAT"
                    ),
                ]
            )
        return {
            "title": _("Stores"),
            "description": _(
                "Orders and revenue are read from each store every 30 minutes."
            ),
            "action_href": self.changelist_url("tenant.Tenant"),
            "empty_icon": "storefront",
            "empty_text": _("No stores yet"),
            "table": {
                "headers": [
                    _("Store"),
                    _("Domain"),
                    _("Plan"),
                    _("Status"),
                    {"content": _("Orders"), "class": "text-right"},
                    {"content": _("Revenue"), "class": "text-right"},
                    _("Last order"),
                ],
                "rows": rows,
            },
        }
