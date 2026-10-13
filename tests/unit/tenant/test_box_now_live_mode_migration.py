"""``tenant`` 0054: today's BoxNow environment becomes each tenant's flag.

The platform URL used to decide the environment, so on the production
deployment every tenant with BoxNow keys (webside) was already live and
must stay so; anywhere else (staging mirrors the production rows) every
tenant must come out off, i.e. stage.
"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from tenant.models import Tenant
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db

migration = import_module("tenant.migrations.0054_tenant_box_now_live_mode")

SCHEMA_EDITOR = SimpleNamespace(connection=connection)


def _live(tenant: Tenant) -> bool:
    return Tenant.objects.get(pk=tenant.pk).box_now_live_mode


@pytest.fixture
def tenants():
    return SimpleNamespace(
        with_keys=store_tenant("mig_bn_keys", box_now_client_id="cid"),
        without_keys=store_tenant("mig_bn_none"),
    )


def test_a_new_tenant_defaults_to_stage():
    assert _live(store_tenant("mig_bn_default")) is False


def test_the_column_default_lives_in_the_database(tenants):
    # ``db_default``: the release still serving during the rollout never
    # names the column, so its INSERTs must still work.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'tenant_tenant' "
            "AND column_name = 'box_now_live_mode'"
        )
        assert cursor.fetchone()[0] == "false"


def test_production_keeps_every_tenant_with_keys_live(settings, tenants):
    settings.SYSTEM_ENV = "production"

    migration.carry_over_environment(apps, SCHEMA_EDITOR)

    assert _live(tenants.with_keys) is True
    assert _live(tenants.without_keys) is False


@pytest.mark.parametrize("system_env", ["staging", "dev", "ci"])
def test_any_other_deployment_leaves_every_tenant_on_stage(
    settings, tenants, system_env
):
    settings.SYSTEM_ENV = system_env

    migration.carry_over_environment(apps, SCHEMA_EDITOR)

    assert _live(tenants.with_keys) is False
    assert _live(tenants.without_keys) is False


def test_the_step_is_idempotent(settings, tenants):
    settings.SYSTEM_ENV = "production"

    migration.carry_over_environment(apps, SCHEMA_EDITOR)
    migration.carry_over_environment(apps, SCHEMA_EDITOR)

    assert _live(tenants.with_keys) is True
