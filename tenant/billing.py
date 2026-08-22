"""Billing-term classification and the daily dunning cycle.

The classifier (``billing_state``) is the single source of truth for
"where does this store stand" — the platform admin's Plan & Billing
page renders it, and ``run_billing_cycle`` (dispatched daily by
``tenant.tasks.process_tenant_billing``) acts on it.

Mechanics
---------
``paid_until`` is the universal TERM END, inclusive — for trials
(stamped at provisioning by ``tenant_create --trial-days``) and paid
plans alike. Empty means the term never expires (legacy rows, the
platform row).

Dunning stages, computed from dates alone (``target_stage``):

    1  term end minus WARN_DAYS   → expiry warning to the owner
    2  day after term end         → expired notice (owner + admins)
    3  term end plus GRACE_DAYS   → suspension (owner + admins)

The only state is "highest stage already notified", stored per term
(``billing_notice_stage`` + ``billing_notice_term``): a run sends at
most the highest NEWLY-reached stage — a store discovered deep past due
gets one email, never a catch-up burst — and moving ``paid_until``
forward makes the recorded stage stale, resetting the pipeline with no
signal. With ``AUTO_SUSPEND`` off (the rollout default) stage 3 is
capped away: warnings and expired notices flow, nothing is suspended,
and no email ever claims a suspension that did not happen.

Everything here runs on the PUBLIC schema only — Tenant rows, stage
bookkeeping, and outbound platform mail. No tenant schema is entered,
so a half-provisioned store can never break the cycle.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def billing_config() -> dict[str, Any]:
    return settings.TENANT_BILLING


def warn_days() -> int:
    return int(billing_config()["WARN_DAYS"])


def billing_state(tenant: Any, today: date) -> str:
    """Classify one tenant's billing situation.

    Precedence mirrors operator urgency: a suspended store is already
    handled; no term means "trial" (for trial plans) or "unbilled" (a
    bookkeeping gap on a paid plan — not "past due", which would accuse
    a store that may simply predate the field); then the term dates
    decide. A trial's term counts exactly like a paid plan's — an
    in-term trial reads "trial", an expiring or lapsed one reads
    "expiring"/"past_due" so it enters the same dunning pipeline.
    """
    from tenant.models import TenantPlan  # noqa: PLC0415

    is_trial = tenant.plan == TenantPlan.TRIAL
    if tenant.suspended_at is not None:
        return "suspended"
    if tenant.paid_until is None:
        return "trial" if is_trial else "unbilled"
    if tenant.paid_until < today:
        return "past_due"
    if tenant.paid_until <= today + timedelta(days=warn_days()):
        return "expiring"
    return "trial" if is_trial else "paid"


def target_stage(
    paid_until: date, today: date, *, warn_days: int, grace_days: int
) -> int:
    """The dunning stage the calendar says this term is at (0–3)."""
    if today > paid_until + timedelta(days=grace_days):
        return 3
    if today > paid_until:
        return 2
    if today >= paid_until - timedelta(days=warn_days):
        return 1
    return 0


# Template slug + whether platform admins get a copy, per stage.
_STAGES: dict[int, tuple[str, bool]] = {
    1: ("expiring", False),
    2: ("expired", True),
    3: ("suspended", True),
}


def _stage_subject(tenant: Any, stage: int) -> str:
    """Subject line, built INSIDE the caller's translation override."""
    from django.utils.formats import date_format  # noqa: PLC0415

    # Aliased to ``_`` (not ``_g``): xgettext extraction is lexical and
    # only recognises the standard keywords, so a ``_g("...")`` string
    # silently never reaches the .po files.
    from django.utils.translation import gettext as _  # noqa: PLC0415

    store = tenant.store_name or tenant.name
    if stage == 1:
        return _("%(store)s — your plan expires on %(date)s") % {
            "store": store,
            "date": date_format(tenant.paid_until),
        }
    if stage == 2:
        return _("%(store)s — your plan has expired") % {"store": store}
    return _("%(store)s — your store has been suspended") % {"store": store}


def _send_stage_email(tenant: Any, stage: int, *, grace_days: int) -> None:
    """One dunning email to the owner; stages 2–3 also copy admins.

    Platform → merchant mail: the platform's own sender
    (``DEFAULT_FROM_EMAIL``), rendered in the tenant's default locale.
    """
    from django.core.mail import (  # noqa: PLC0415
        EmailMultiAlternatives,
        mail_admins,
    )
    from django.template.loader import render_to_string  # noqa: PLC0415
    from django.utils import translation  # noqa: PLC0415

    from core.utils.email_context import build_email_context  # noqa: PLC0415
    from tenant.models import TenantPlan  # noqa: PLC0415

    slug, copy_admins = _STAGES[stage]
    context = build_email_context(
        store_name=tenant.store_name or tenant.name,
        plan_display=tenant.get_plan_display(),
        is_trial=tenant.plan == TenantPlan.TRIAL,
        paid_until=tenant.paid_until,
        grace_days=grace_days,
        owner_email=tenant.owner_email,
    )

    locale = tenant.default_locale or settings.LANGUAGE_CODE
    with translation.override(locale):
        subject = _stage_subject(tenant, stage)
        html_body = render_to_string(f"emails/billing/{slug}.html", context)
        text_body = render_to_string(f"emails/billing/{slug}.txt", context)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[tenant.owner_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)

    if copy_admins:
        # Ops trail — a no-op when ADMINS (ADMIN_EMAIL) is unset, and
        # never allowed to fail the merchant-facing send it follows.
        mail_admins(
            subject=f"tenant billing stage {stage}: {tenant.schema_name}",
            message=(
                f"Store: {tenant.store_name or tenant.name}\n"
                f"Schema: {tenant.schema_name}\n"
                f"Plan: {tenant.plan}\n"
                f"Term ended: {tenant.paid_until}\n"
                f"Stage: {stage} ({slug})"
            ),
            fail_silently=True,
        )


def run_billing_cycle() -> dict[str, int]:
    """Process the whole estate once; returns a JSON-safe summary.

    Per-tenant failures are contained (logged + counted) so one broken
    row cannot halt dunning for every other store. The stage record is
    written only AFTER a successful send — at-least-once semantics: a
    crashed run re-sends rather than silently skipping a notice.
    """
    from django.utils import timezone  # noqa: PLC0415
    from django_tenants.utils import get_public_schema_name  # noqa: PLC0415

    from tenant.lifecycle import (  # noqa: PLC0415
        PROTECTED_SCHEMAS,
        suspend_tenant,
    )
    from tenant.models import SuspendedReason, Tenant  # noqa: PLC0415

    conf = billing_config()
    warn = int(conf["WARN_DAYS"])
    grace = int(conf["GRACE_DAYS"])
    auto_suspend = bool(conf["AUTO_SUSPEND"])
    today = timezone.localdate()

    summary = {"warned": 0, "expired": 0, "suspended": 0, "errors": 0}
    stage_counters = {1: "warned", 2: "expired", 3: "suspended"}

    tenants = (
        Tenant.objects.filter(
            is_active=True,
            suspended_at__isnull=True,
            paid_until__isnull=False,
        )
        .exclude(schema_name=get_public_schema_name())
        .order_by("pk")
    )

    for tenant in tenants:
        try:
            # A stage is only valid for the term it was recorded
            # against — a renewal makes it stale.
            stage = (
                tenant.billing_notice_stage
                if tenant.billing_notice_term == tenant.paid_until
                else 0
            )
            target = target_stage(
                tenant.paid_until, today, warn_days=warn, grace_days=grace
            )
            if not auto_suspend:
                # Notify-only rollout: no suspension, and no email that
                # claims one happened. When the switch flips, overdue
                # stores move 2 → 3 on the next run.
                target = min(target, 2)
            if target <= stage:
                continue
            if target == 3 and tenant.schema_name in PROTECTED_SCHEMAS:
                # suspend_tenant would refuse anyway; skipping before
                # the email keeps a protected store from receiving a
                # suspension notice that cannot come true.
                logger.warning(
                    "billing: protected tenant %s is past grace — left running",
                    tenant.schema_name,
                )
                continue

            # Email BEFORE the state changes: if the send raises, the
            # stage stays unrecorded and the tenant stays active, so
            # the next run retries the whole step.
            _send_stage_email(tenant, target, grace_days=grace)
            if target == 3:
                suspend_tenant(tenant, reason=SuspendedReason.BILLING)

            tenant.billing_notice_stage = target
            tenant.billing_notice_term = tenant.paid_until
            tenant.save(
                update_fields=[
                    "billing_notice_stage",
                    "billing_notice_term",
                ]
            )
            summary[stage_counters[target]] += 1
            logger.info(
                "billing: %s reached stage %d (term ended %s)",
                tenant.schema_name,
                target,
                tenant.paid_until,
            )
        except Exception:
            logger.exception(
                "billing: failed processing tenant %s", tenant.schema_name
            )
            summary["errors"] += 1

    return summary
