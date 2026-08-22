"""The billing classifier, the dunning cycle, and the lifecycle helpers.

The classifier decides what an operator sees AND what the daily task
does; the stage bookkeeping is what makes the task idempotent and
renewal-resetting; ``suspended_reason`` is what keeps auto-reactivation
from ever lifting an abuse suspension. All of that is pinned here.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from tenant.billing import billing_state, run_billing_cycle, target_stage
from tenant.lifecycle import activate_tenant, suspend_tenant
from tenant.models import SuspendedReason, Tenant

_TODAY = date(2026, 8, 22)

_BILLING = {"WARN_DAYS": 14, "GRACE_DAYS": 7, "AUTO_SUSPEND": True}


def _tenant_row(**kwargs) -> Tenant:
    """Unsaved Tenant for pure classifier calls."""
    defaults = {"plan": "pro", "paid_until": None, "suspended_at": None}
    defaults.update(kwargs)
    return Tenant(**defaults)


_SEQ = iter(range(10_000))


def _make_tenant(**kwargs) -> Tenant:
    """Saved Tenant without Postgres DDL (no schema is ever created)."""
    n = next(_SEQ)
    defaults = {
        "schema_name": f"dunning_t{n}",
        "name": f"Dunning Tenant {n}",
        "slug": f"dunning-tenant-{n}",
        "owner_email": f"owner-dunning-{n}@example.com",
        "plan": "pro",
    }
    defaults.update(kwargs)
    t = Tenant(**defaults)
    t.auto_create_schema = False
    t.save()
    return t


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class TestBillingState:
    @pytest.fixture(autouse=True)
    def _config(self, settings):
        settings.TENANT_BILLING = dict(_BILLING)

    def test_suspended_wins_over_everything(self):
        t = _tenant_row(
            plan="trial",
            paid_until=_TODAY - timedelta(days=30),
            suspended_at=timezone.now(),
        )
        assert billing_state(t, _TODAY) == "suspended"

    def test_a_trial_with_no_term_never_expires(self):
        """Legacy rows and the platform row — the explicit escape hatch."""
        assert billing_state(_tenant_row(plan="trial"), _TODAY) == "trial"

    def test_a_paid_plan_with_no_term_is_unbilled_not_past_due(self):
        """A bookkeeping gap must not read as an overdue store."""
        assert billing_state(_tenant_row(), _TODAY) == "unbilled"

    def test_a_trial_term_counts_like_a_paid_one(self):
        """Provisioned trials enter the same pipeline as paid plans."""
        past = _tenant_row(plan="trial", paid_until=_TODAY - timedelta(days=1))
        soon = _tenant_row(plan="trial", paid_until=_TODAY + timedelta(days=3))
        far = _tenant_row(plan="trial", paid_until=_TODAY + timedelta(days=60))
        assert billing_state(past, _TODAY) == "past_due"
        assert billing_state(soon, _TODAY) == "expiring"
        assert billing_state(far, _TODAY) == "trial"

    def test_a_lapsed_term_is_past_due(self):
        t = _tenant_row(paid_until=_TODAY - timedelta(days=1))
        assert billing_state(t, _TODAY) == "past_due"

    def test_a_term_ending_today_is_expiring_not_past_due(self):
        """Paid Until is inclusive — the term covers that day."""
        assert billing_state(_tenant_row(paid_until=_TODAY), _TODAY) == (
            "expiring"
        )

    def test_the_warning_window_edges(self):
        edge = _tenant_row(paid_until=_TODAY + timedelta(days=14))
        beyond = _tenant_row(paid_until=_TODAY + timedelta(days=15))
        assert billing_state(edge, _TODAY) == "expiring"
        assert billing_state(beyond, _TODAY) == "paid"


class TestTargetStage:
    def _stage(self, paid_until: date) -> int:
        return target_stage(paid_until, _TODAY, warn_days=14, grace_days=7)

    def test_boundaries(self):
        assert self._stage(_TODAY + timedelta(days=15)) == 0
        assert self._stage(_TODAY + timedelta(days=14)) == 1  # warn edge
        assert self._stage(_TODAY) == 1  # last covered day
        assert self._stage(_TODAY - timedelta(days=1)) == 2  # lapsed
        assert self._stage(_TODAY - timedelta(days=7)) == 2  # grace edge
        assert self._stage(_TODAY - timedelta(days=8)) == 3  # past grace


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLifecycle:
    def test_suspend_stamps_state_and_reason(self):
        t = _make_tenant()
        assert suspend_tenant(t, reason=SuspendedReason.MANUAL) is True
        t.refresh_from_db()
        assert t.is_active is False
        assert t.suspended_at is not None
        assert t.suspended_reason == SuspendedReason.MANUAL

    def test_re_suspend_is_a_noop_and_keeps_the_original_reason(self):
        """The billing task must never relabel an abuse suspension,
        nor reset the 24h destroy cooldown."""
        t = _make_tenant()
        suspend_tenant(t, reason=SuspendedReason.MANUAL)
        t.refresh_from_db()
        stamped = t.suspended_at
        assert suspend_tenant(t, reason=SuspendedReason.BILLING) is False
        t.refresh_from_db()
        assert t.suspended_reason == SuspendedReason.MANUAL
        assert t.suspended_at == stamped

    def test_activate_clears_everything(self):
        t = _make_tenant(paid_until=_TODAY - timedelta(days=30))
        suspend_tenant(t, reason=SuspendedReason.BILLING)
        assert activate_tenant(t) is True
        t.refresh_from_db()
        assert t.is_active is True
        assert t.suspended_at is None
        assert t.suspended_reason == ""

    def test_protected_schemas_are_refused(self):
        t = _make_tenant()
        with patch(
            "tenant.lifecycle.PROTECTED_SCHEMAS",
            frozenset({t.schema_name}),
        ):
            assert suspend_tenant(t, reason=SuspendedReason.MANUAL) is False
            assert activate_tenant(t) is False
        t.refresh_from_db()
        assert t.is_active is True


# ---------------------------------------------------------------------------
# The dunning cycle
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRunBillingCycle:
    @pytest.fixture(autouse=True)
    def _config(self, settings, mailoutbox):
        settings.TENANT_BILLING = dict(_BILLING)
        # A developer's .env ADMIN_EMAIL would add mail_admins copies to
        # the outbox and skew the counts — pin the ops channel shut.
        settings.ADMINS = []
        self.outbox = mailoutbox

    def _today(self) -> date:
        return timezone.localdate()

    def test_warns_inside_the_window_once(self):
        t = _make_tenant(paid_until=self._today() + timedelta(days=5))
        summary = run_billing_cycle()
        assert summary["warned"] == 1
        assert len(self.outbox) == 1
        assert self.outbox[0].to == [t.owner_email]
        t.refresh_from_db()
        assert t.billing_notice_stage == 1
        assert t.billing_notice_term == t.paid_until

        # Same day, second run — idempotent, nothing new.
        summary = run_billing_cycle()
        assert summary["warned"] == 0
        assert len(self.outbox) == 1

    def test_expired_notice_the_day_after_the_term(self):
        t = _make_tenant(paid_until=self._today() - timedelta(days=1))
        summary = run_billing_cycle()
        assert summary["expired"] == 1
        t.refresh_from_db()
        assert t.billing_notice_stage == 2
        assert t.is_active is True  # grace period — still running

    def test_past_grace_suspends_with_billing_reason(self):
        t = _make_tenant(paid_until=self._today() - timedelta(days=8))
        summary = run_billing_cycle()
        assert summary["suspended"] == 1
        # Highest NEWLY-reached stage only: one email, no catch-up burst.
        assert len(self.outbox) == 1
        t.refresh_from_db()
        assert t.is_active is False
        assert t.suspended_reason == SuspendedReason.BILLING
        assert t.billing_notice_stage == 3

    def test_notify_only_mode_caps_at_expired(self, settings):
        """AUTO_SUSPEND off: no suspension, and no email claiming one."""
        settings.TENANT_BILLING = {**_BILLING, "AUTO_SUSPEND": False}
        t = _make_tenant(paid_until=self._today() - timedelta(days=30))
        summary = run_billing_cycle()
        assert summary["expired"] == 1
        assert summary["suspended"] == 0
        t.refresh_from_db()
        assert t.is_active is True
        assert t.billing_notice_stage == 2

        # Flipping the switch later moves 2 → 3 on the next run.
        settings.TENANT_BILLING = dict(_BILLING)
        summary = run_billing_cycle()
        assert summary["suspended"] == 1
        t.refresh_from_db()
        assert t.is_active is False

    def test_a_renewal_resets_the_stage(self):
        t = _make_tenant(paid_until=self._today() - timedelta(days=1))
        run_billing_cycle()
        t.refresh_from_db()
        assert t.billing_notice_stage == 2

        # Payment recorded — new term, well in the future.
        t.paid_until = self._today() + timedelta(days=365)
        t.save(update_fields=["paid_until"])
        summary = run_billing_cycle()
        assert summary == {
            "warned": 0,
            "expired": 0,
            "suspended": 0,
            "errors": 0,
        }
        assert len(self.outbox) == 1  # still just the stage-2 email

    def test_ignores_stores_without_a_term(self):
        _make_tenant(paid_until=None)
        summary = run_billing_cycle()
        assert summary == {
            "warned": 0,
            "expired": 0,
            "suspended": 0,
            "errors": 0,
        }
        assert len(self.outbox) == 0

    def test_a_protected_store_is_never_suspended_or_emailed(self):
        """A suspension notice that cannot come true must not be sent."""
        t = _make_tenant(paid_until=self._today() - timedelta(days=30))
        with patch(
            "tenant.lifecycle.PROTECTED_SCHEMAS",
            frozenset({t.schema_name}),
        ):
            summary = run_billing_cycle()
        assert summary["suspended"] == 0
        assert len(self.outbox) == 0
        t.refresh_from_db()
        assert t.is_active is True

    def test_one_broken_tenant_does_not_halt_the_estate(self):
        broken = _make_tenant(paid_until=self._today() - timedelta(days=1))
        fine = _make_tenant(paid_until=self._today() - timedelta(days=1))

        real_send = "tenant.billing._send_stage_email"

        def _explode_for_broken(tenant, stage, *, grace_days):
            if tenant.pk == broken.pk:
                raise RuntimeError("smtp down for this one")

        with patch(real_send, side_effect=_explode_for_broken):
            summary = run_billing_cycle()

        assert summary["errors"] == 1
        assert summary["expired"] == 1
        broken.refresh_from_db()
        fine.refresh_from_db()
        # The failed send left no stage behind — next run retries it.
        assert broken.billing_notice_stage == 0
        assert fine.billing_notice_stage == 2


# ---------------------------------------------------------------------------
# Renewal-driven auto-reactivation (tenant/signals.py)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReactivateOnRenewal:
    def _billing_suspended(self) -> Tenant:
        t = _make_tenant(paid_until=timezone.localdate() - timedelta(days=30))
        suspend_tenant(t, reason=SuspendedReason.BILLING)
        t.refresh_from_db()
        assert t.is_active is False
        return t

    def test_recording_a_renewal_reactivates_immediately(self):
        t = self._billing_suspended()
        t.paid_until = timezone.localdate() + timedelta(days=30)
        t.save(update_fields=["paid_until"])
        t.refresh_from_db()
        assert t.is_active is True
        assert t.suspended_at is None
        assert t.suspended_reason == ""

    def test_a_renewal_dated_today_counts(self):
        """The term is inclusive — paid through today means active."""
        t = self._billing_suspended()
        t.paid_until = timezone.localdate()
        t.save(update_fields=["paid_until"])
        t.refresh_from_db()
        assert t.is_active is True

    def test_a_still_past_term_does_not_reactivate(self):
        t = self._billing_suspended()
        t.paid_until = timezone.localdate() - timedelta(days=1)
        t.save(update_fields=["paid_until"])
        t.refresh_from_db()
        assert t.is_active is False

    def test_a_manual_suspension_is_never_lifted_by_bookkeeping(self):
        t = _make_tenant(paid_until=timezone.localdate() - timedelta(days=30))
        suspend_tenant(t, reason=SuspendedReason.MANUAL)
        t.paid_until = timezone.localdate() + timedelta(days=365)
        t.save(update_fields=["paid_until"])
        t.refresh_from_db()
        assert t.is_active is False
        assert t.suspended_reason == SuspendedReason.MANUAL
