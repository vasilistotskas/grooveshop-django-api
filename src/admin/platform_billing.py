"""Plan & Billing reference page for the platform control plane.

A custom Unfold page (``UnfoldSiteViewMixin`` — verified against
django-unfold 0.108.0) mounted only on ``PlatformAdminSite``, so it is
structurally absent from every merchant admin rather than
reachable-but-403.

Two jobs:

- DOCUMENT how plan & billing actually works today — plans are
  platform-set labels, ``paid_until`` is recorded manually and nothing
  suspends automatically when it lapses. Writing that down here is what
  keeps a new operator from assuming enforcement that does not exist.
- SURFACE the billing state of the estate — trials, terms expiring
  soon, terms already past — because until enforcement is automated,
  this page is the only place a lapsed term becomes visible.

Row data is public-schema only (``Tenant`` rows); no tenant schema is
entered, so this page can never 500 on a half-provisioned store.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView
from unfold.views import UnfoldSiteViewMixin

# Classification lives in tenant/billing.py — the same ``billing_state``
# the daily dunning task acts on, so this page can never disagree with
# what the automation will do.
from tenant.billing import billing_config, billing_state


def _billing_rows(today: date) -> list[dict[str, Any]]:
    """One row per store (the public schema is the platform, not a store)."""
    from django.apps import apps
    from django_tenants.utils import get_public_schema_name

    Tenant = apps.get_model("tenant", "Tenant")
    rows: list[dict[str, Any]] = []
    for tenant in (
        Tenant.objects.exclude(schema_name=get_public_schema_name())
        .prefetch_related("domains")
        .order_by("name")
    ):
        primary = next((d for d in tenant.domains.all() if d.is_primary), None)
        rows.append(
            {
                "tenant": tenant,
                "name": tenant.store_name or tenant.name,
                "schema": tenant.schema_name,
                "domain": primary.domain if primary else "",
                "paid_until": tenant.paid_until,
                "state": billing_state(tenant, today),
            }
        )
    return rows


def _billing_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape rows for ``unfold/components/table.html``.

    Badge cells are rendered labels (``tenant.admin_labels``, the same
    vocabulary as the Tenants list); tenant-supplied names/domains stay
    plain strings so the component escapes them.
    """
    from django.utils.formats import date_format

    from tenant.admin_labels import billing_badge, plan_badge

    table_rows = [
        [
            row["name"],
            row["domain"] or "—",
            plan_badge(row["tenant"]),
            date_format(row["paid_until"]) if row["paid_until"] else "—",
            billing_badge(row["state"]),
        ]
        for row in rows
    ]

    # The module-level lazy ``_``: evaluated at render time in the
    # request's locale, and — unlike a ``gettext``-under-alias local —
    # a keyword xgettext actually extracts.
    return {
        "headers": [
            _("Store"),
            _("Domain"),
            _("Plan"),
            _("Paid until"),
            _("Billing status"),
        ],
        "rows": table_rows,
    }


class PlanBillingView(UnfoldSiteViewMixin, TemplateView):
    """Reachable only through ``PlatformAdminSite.get_urls`` — the
    site's ``admin_view`` wrapper already enforces the platform gate
    (superuser + ``PlatformStaffBackend`` session), so no model
    permission is layered on top.
    """

    title = _("Plan & Billing")
    permission_required = ()
    template_name = "admin/platform_billing.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from django.utils import timezone

        context = super().get_context_data(**kwargs)

        from django.urls import reverse

        from tenant.admin_labels import BILLING_BADGES, plan_badges

        today = timezone.localdate()
        rows = _billing_rows(today)
        states = [row["state"] for row in rows]
        conf = billing_config()
        counters = [
            ("trial", _("Trials"), states.count("trial")),
            ("paid", _("Paid & current"), states.count("paid")),
            (
                "expiring",
                _("Expiring within %(days)d days")
                % {"days": conf["WARN_DAYS"]},
                states.count("expiring"),
            ),
            (
                "past_due",
                _("Past due / no term"),
                states.count("past_due") + states.count("unbilled"),
            ),
        ]
        context.update(
            {
                "billing_rows": rows,
                "billing_table": _billing_table(rows),
                # Tone and icon from the same vocabulary as the badges.
                "billing_kpis": [
                    {
                        "label": text,
                        "value": value,
                        "tone": BILLING_BADGES[state][1],
                        "icon": BILLING_BADGES[state][2],
                    }
                    for state, text, value in counters
                ],
                "plan_badges": plan_badges(),
                # This page exists only on the platform site.
                "tenants_url": reverse(
                    f"{self.admin_site.name}:tenant_tenant_changelist"
                ),
                "expiry_warning_days": conf["WARN_DAYS"],
                "billing_grace_days": conf["GRACE_DAYS"],
                "billing_auto_suspend": conf["AUTO_SUSPEND"],
            }
        )
        return context
