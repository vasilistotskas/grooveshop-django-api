"""How a store's plan, lifecycle and billing state read in the admin.

One vocabulary for every surface that shows them - the Tenants list,
the Plan & Billing page and the control-plane dashboard - rendered
through Unfold's own ``unfold/helpers/label.html`` so the badges carry
its palette and dark-mode classes (``@display(label=...)`` cannot take
an icon; the template can).
"""

from __future__ import annotations

from typing import Any

from django.template.loader import render_to_string
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _

from tenant.models import Tenant, TenantPlan

PLAN_BADGES: dict[str, tuple[str, str]] = {
    TenantPlan.TRIAL: ("schedule", "warning"),
    TenantPlan.BASIC: ("storefront", "info"),
    TenantPlan.PRO: ("rocket_launch", "primary"),
    TenantPlan.ENTERPRISE: ("workspace_premium", "success"),
}

# Keyed by ``tenant.billing.billing_state()``'s return value.
BILLING_BADGES: dict[str, tuple[Any, str, str]] = {
    "suspended": (_("Suspended"), "danger", "pause_circle"),
    "past_due": (_("Past due"), "danger", "event_busy"),
    "expiring": (_("Expires soon"), "warning", "hourglass_top"),
    "trial": (_("Trial"), "warning", "schedule"),
    "unbilled": (_("No term recorded"), "info", "contract"),
    "paid": (_("Paid"), "success", "check_circle"),
}


def label(text: Any, variant: str, icon: str | None = None) -> SafeString:
    return render_to_string(
        "unfold/helpers/label.html",
        {"text": text, "variant": variant, "icon": icon},
    )


def plan_badge(tenant: Tenant) -> SafeString:
    icon, variant = PLAN_BADGES[tenant.plan]
    return label(tenant.get_plan_display(), variant, icon)


def plan_badges() -> list[SafeString]:
    """Every plan's badge, in plan order."""
    return [
        label(TenantPlan(plan).label, variant, icon)
        for plan, (icon, variant) in PLAN_BADGES.items()
    ]


def status_badge(tenant: Tenant) -> SafeString:
    """Suspended is distinct from inactive - different operations.

    A suspended store is mid-lifecycle (24h cooldown before it can be
    destroyed); an inactive one was switched off.
    """
    if tenant.suspended_at is not None:
        return label(_("Suspended"), "danger", "pause_circle")
    if not tenant.is_active:
        return label(_("Inactive"), "warning", "visibility_off")
    return label(_("Live"), "success", "check_circle")


def billing_badge(state: str) -> SafeString:
    text, variant, icon = BILLING_BADGES[state]
    return label(text, variant, icon)
