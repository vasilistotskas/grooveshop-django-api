"""Operational alert switches are per store.

A store mutes an alert kind with its own ``OPS_ALERT_<KIND>_ENABLED``
``Setting`` row (``shipping.alerts.OpsAlertKind``). Rows live in each
tenant schema, so one store's mute must never silence another store, nor
the platform's public schema. The main suite strips multi-tenancy and
caches nothing for ``extra_settings``, so it cannot see a leak through the
tenant-keyed setting cache; this lane runs the real router and cache keys.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django_tenants.utils import schema_context

from shipping.alerts import OpsAlertKind, ops_alert_enabled, send_ops_alert

KIND = OpsAlertKind.SHIPMENT_REJECTED


def _set(enabled: bool) -> None:
    from extra_settings.models import Setting

    Setting.objects.update_or_create(
        name=KIND.setting_name,
        defaults={"value_type": "bool", "value_bool": enabled},
    )


@pytest.fixture(autouse=True)
def _cleanup(mt_tenant):
    yield
    from extra_settings.models import Setting

    for schema in (mt_tenant.schema_name, "public"):
        with schema_context(schema):
            Setting.objects.filter(name=KIND.setting_name).delete()


@pytest.mark.django_db
def test_a_mute_in_one_store_does_not_reach_another_schema(mt_tenant):
    with schema_context(mt_tenant.schema_name):
        _set(False)
        assert ops_alert_enabled(KIND) is False

    with schema_context("public"):
        assert ops_alert_enabled(KIND) is True, (
            "the tenant's mute silenced the public schema: the setting "
            "rows or their cache keys are not schema-scoped"
        )


@pytest.mark.django_db
def test_a_store_keeps_its_own_choice_when_another_changes(mt_tenant):
    with schema_context(mt_tenant.schema_name):
        _set(False)

    with schema_context("public"):
        _set(True)
        assert ops_alert_enabled(KIND) is True

    with schema_context(mt_tenant.schema_name):
        assert ops_alert_enabled(KIND) is False, (
            "another schema's write to the same setting name overwrote "
            "the store's mute"
        )


@pytest.mark.django_db
def test_the_gate_mails_one_store_and_not_the_one_that_muted(mt_tenant):
    with schema_context(mt_tenant.schema_name):
        _set(False)

    with (
        patch("shipping.alerts.send_mail") as mail,
        patch(
            "tenant.credentials.tenant_admin_recipients",
            return_value=["ops@example.com"],
        ),
    ):
        with schema_context(mt_tenant.schema_name):
            assert send_ops_alert(kind=KIND, subject="s", message="m") is False
        assert mail.call_count == 0

        with schema_context("public"):
            assert send_ops_alert(kind=KIND, subject="s", message="m") is True
        assert mail.call_count == 1
