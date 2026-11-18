"""The deploy's data-shape backfills reach suspended stores; its index
settings step does not.

A backfill brings a store's data up to date with the code and starts no
work, like a retention sweep, so a store suspended through a few deploys
must not be missing what they created when it is reactivated. Applying
Meilisearch index settings calls a service on the store's behalf, so it
stays with the active stores (``TenantCommandMixin --all-tenants``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import connection

from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def stores():
    from tenant.lifecycle import suspend_tenant
    from tenant.models import SuspendedReason

    active = store_tenant("backfill_active")
    suspended = store_tenant("backfill_suspended")
    with patch("tenant.tasks.flush_tenant_media_task.apply_async"):
        assert suspend_tenant(suspended, reason=SuspendedReason.MANUAL)
    return active, suspended


def _recording(seen: list[str], result=None):
    def record(*_args, **_kwargs):
        seen.append(connection.schema_name)
        return result

    return record


def test_recommendation_slots_are_seeded_in_a_suspended_store(stores):
    active, suspended = stores
    seen: list[str] = []

    with patch(
        "recommendation.presets.seed_recommendation_slots",
        side_effect=_recording(seen, 0),
    ):
        call_command("backfill_recommendation_slots")

    assert {active.schema_name, suspended.schema_name} <= set(seen)
    assert "public" not in seen


def test_extra_settings_are_backfilled_in_a_suspended_store(stores):
    active, suspended = stores
    seen: list[str] = []

    with (
        patch(
            "extra_settings.models.Setting.set_defaults_from_settings",
            side_effect=_recording(seen),
        ),
        patch("tenant.settings_metadata.sync_setting_metadata"),
    ):
        call_command("backfill_extra_settings_defaults")

    assert {active.schema_name, suspended.schema_name} <= set(seen)
    assert "public" not in seen


def test_index_settings_reach_only_the_active_stores(stores):
    from meili.management.commands.meilisearch_apply_settings import Command

    active, suspended = stores

    targets = {
        tenant.schema_name
        for tenant in Command().get_target_tenants({"all_tenants": True})
    }

    assert active.schema_name in targets
    assert suspended.schema_name not in targets
