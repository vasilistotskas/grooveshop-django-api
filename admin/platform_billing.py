"""Plan & Billing reference page for the platform control plane.

A custom Unfold page (``UnfoldSiteViewMixin`` — verified against
django-unfold 0.104.1) mounted only on ``PlatformAdminSite``, so it is
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

# Billing states, in the order an operator should care about them.
# Presentation per state: (label, unfold label tone, material icon).
#
# Canonical map lives in tenant/admin.py (the Tenants changelist's own
# billing-state badge column) — imported lazily below to avoid a
# module-load-time circular import, mirroring how ``_billing_table``
# already borrows ``_PLAN_BADGES``/``_unfold_label`` from there.


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
                "name": tenant.store_name or tenant.name,
                "schema": tenant.schema_name,
                "domain": primary.domain if primary else "",
                "plan": tenant.plan,
                "plan_display": tenant.get_plan_display(),
                "paid_until": tenant.paid_until,
                "state": billing_state(tenant, today),
            }
        )
    return rows


def _billing_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape rows for ``unfold/components/table.html``.

    Badge cells go through ``_unfold_label`` (fixed strings only) and
    are safe; tenant-supplied names/domains stay plain strings so the
    component escapes them — same contract as the dashboard table.
    """
    from django.utils.formats import date_format
    from django.utils.safestring import mark_safe

    # Same badge maps + renderer the Tenants changelist uses, so the two
    # surfaces cannot drift as tiers/states change.
    from tenant.admin import (
        _PLAN_BADGES,
        _STATE_BADGES,
        _unfold_label,
    )

    table_rows = []
    for row in rows:
        plan_icon, plan_tone = _PLAN_BADGES.get(row["plan"], ("help", "info"))
        state_label, state_tone, state_icon = _STATE_BADGES[row["state"]]
        table_rows.append(
            [
                row["name"],
                row["domain"] or "—",
                mark_safe(
                    _unfold_label(row["plan_display"], plan_tone, plan_icon)
                ),
                date_format(row["paid_until"]) if row["paid_until"] else "—",
                mark_safe(
                    _unfold_label(str(state_label), state_tone, state_icon)
                ),
            ]
        )

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

        today = timezone.localdate()
        rows = _billing_rows(today)
        states = [row["state"] for row in rows]
        conf = billing_config()
        context.update(
            {
                "billing_rows": rows,
                "billing_table": _billing_table(rows),
                "billing_trial_count": states.count("trial"),
                "billing_paid_count": states.count("paid"),
                "billing_expiring_count": states.count("expiring"),
                "billing_past_due_count": states.count("past_due")
                + states.count("unbilled"),
                "expiry_warning_days": conf["WARN_DAYS"],
                "billing_grace_days": conf["GRACE_DAYS"],
                "billing_auto_suspend": conf["AUTO_SUSPEND"],
            }
        )
        return context
