"""``emails.0004``: the ``OPS_ALERT_<KIND>_ENABLED`` rows 0003 left behind.

0003 copied each muted setting into an ``EmailSwitch`` and kept the rows for
the release still reading them. Once that release is gone, 0004 deletes them
and nothing else: other settings and the switches stay.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from extra_settings.models import Setting

from emails.models import EmailSwitch

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("tenant")]

migration = importlib.import_module(
    "emails.migrations.0004_drop_ops_alert_settings"
)

KEPT = "CONTACT_RETENTION_DAYS"


def run_migration() -> None:
    migration.delete_settings(apps, SimpleNamespace(connection=connection))


def test_it_declares_the_import_it_follows():
    assert migration.Migration.contract_of == [
        ("emails", "0003_import_ops_alert_switches")
    ]


def test_every_ops_alert_setting_row_is_deleted():
    Setting.objects.create(
        name="OPS_ALERT_REFUND_FAILED_ENABLED",
        value_type="bool",
        value_bool=False,
    )
    Setting.objects.create(
        name="OPS_ALERT_REVIEW_PENDING_ENABLED",
        value_type="bool",
        value_bool=True,
    )

    run_migration()

    assert not Setting.objects.filter(name__startswith="OPS_ALERT_").exists()


def test_other_settings_and_the_switches_are_kept():
    Setting.objects.update_or_create(
        name=KEPT, defaults={"value_type": "int", "value_int": 30}
    )
    Setting.objects.create(
        name="OPS_ALERT_REFUND_FAILED_ENABLED",
        value_type="bool",
        value_bool=False,
    )
    EmailSwitch.objects.create(type_key="ops.refund_failed")

    run_migration()

    assert Setting.objects.filter(name=KEPT).exists()
    assert set(EmailSwitch.objects.values_list("type_key", flat=True)) == {
        "ops.refund_failed"
    }
