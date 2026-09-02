"""``tenant.lifecycle`` protection and destroy gates.

Protection is the ``Tenant.is_protected`` row flag (plus the public
schema by construction); the destroy gates are one function that every
caller goes through.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from tenant.lifecycle import (
    SUSPEND_COOLDOWN,
    destroy_refusal,
    is_protected_tenant,
)
from tenant.models import Tenant


def _tenant(**kwargs) -> Tenant:
    defaults = {
        "schema_name": "acme",
        "is_active": False,
        "is_protected": False,
        "suspended_at": timezone.now() - SUSPEND_COOLDOWN - timedelta(hours=1),
    }
    defaults.update(kwargs)
    return Tenant(**defaults)


def test_public_schema_is_protected_by_construction():
    assert is_protected_tenant(_tenant(schema_name="public")) is True


def test_flag_protects_any_schema():
    assert is_protected_tenant(_tenant(is_protected=True)) is True
    assert is_protected_tenant(_tenant()) is False


def test_protected_wins_over_every_other_gate():
    assert destroy_refusal(_tenant(is_protected=True)) == "protected"
    assert destroy_refusal(_tenant(schema_name="public")) == "protected"


def test_live_tenant_must_be_suspended_first():
    assert destroy_refusal(_tenant(is_active=True)) == "not_suspended"
    assert destroy_refusal(_tenant(suspended_at=None)) == "not_suspended"


def test_cooldown_counts_from_suspension():
    now = timezone.now()
    recent = _tenant(suspended_at=now - SUSPEND_COOLDOWN + timedelta(minutes=1))
    assert destroy_refusal(recent, now=now) == "cooldown"
    elapsed = _tenant(suspended_at=now - SUSPEND_COOLDOWN)
    assert destroy_refusal(elapsed, now=now) is None


def test_suspended_past_cooldown_is_destroyable():
    assert destroy_refusal(_tenant()) is None
