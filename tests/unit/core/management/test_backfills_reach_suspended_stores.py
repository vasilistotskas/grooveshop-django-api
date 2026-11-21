"""A command that only brings a store's data up to date reaches suspended
stores; one that calls a service for the store or sends mail does not.

A backfill writes what a release added to the store's own schema and starts
no work, like a retention sweep, so a store suspended through a few releases
must not come back without it. ``TenantCommandMixin.include_suspended_stores``
is where each command says which it is: ``--all-tenants`` and the deploy
backfills' own selection read ``target_stores()``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.management import (
    call_command,
    get_commands,
    load_command_class,
)
from django.db import connection

from core.management.tenant_mixin import TenantCommandMixin
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def stores():
    from tenant.enum import SuspendedReason
    from tenant.lifecycle import suspend_tenant

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


#: Every command on ``TenantCommandMixin``, and whether it reaches
#: suspended stores. True only where the command reshapes the store's own
#: data: the builder commands read the storefront's tenant-free block
#: catalogue and its static images, nothing on the store's behalf.
INCLUDE_SUSPENDED_STORES = {
    "backfill_extra_settings_defaults": True,
    "backfill_line_snapshots": True,
    "backfill_recommendation_slots": True,
    "backfill_turnover_snapshots": True,
    "builder_rewrite_backups": True,
    # A retention sweep, like its beat fan-out.
    "expire_notifications": True,
    "heal_discount_allocation": True,
    "import_builder_media": True,
    "migrate_builder_media": True,
    "normalise_rich_text": True,
    "persist_block_upgrades": True,
    # Meilisearch is a service called per store.
    "meilisearch_apply_settings": False,
    "meilisearch_drop": False,
    "meilisearch_inspect_index": False,
    "meilisearch_sync_all_indexes": False,
    "meilisearch_sync_index": False,
    "meilisearch_update_index_settings": False,
    "meilisearch_update_ranking": False,
    # Calls ACS, and mails customers unless --silent.
    "reconcile_acs_cod": False,
    # Order transitions whose signals start work (history, the outbox).
    "reconcile_acs_order_status": False,
    # A readiness report before a store issues receipts, not a backfill.
    "mydata_readiness": False,
}


def _mixin_commands() -> dict[str, TenantCommandMixin]:
    found = {}
    for name, app in get_commands().items():
        command = load_command_class(app, name)
        if isinstance(command, TenantCommandMixin):
            found[name] = command
    return found


def test_every_mixin_command_declares_whether_it_reaches_suspended_stores():
    assert set(_mixin_commands()) == set(INCLUDE_SUSPENDED_STORES)


@pytest.mark.parametrize(
    ("name", "expected"), sorted(INCLUDE_SUSPENDED_STORES.items())
)
def test_the_command_reaches_suspended_stores_only_if_it_reshapes_data(
    name, expected
):
    assert _mixin_commands()[name].include_suspended_stores is expected


def test_all_tenants_reaches_a_suspended_store_for_a_data_backfill(stores):
    active, suspended = stores
    seen: list[str] = []

    with patch(
        "order.management.commands.backfill_line_snapshots."
        "backfill_line_snapshots",
        side_effect=_recording(seen, 0),
    ):
        call_command("backfill_line_snapshots", all_tenants=True)

    assert {active.schema_name, suspended.schema_name} <= set(seen)
    assert "public" not in seen
