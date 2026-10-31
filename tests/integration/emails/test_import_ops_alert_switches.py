"""``emails.0003``: a store that muted an operational alert before the
alerts became email types.

A store muted an alert with its own ``OPS_ALERT_<KIND>_ENABLED`` setting.
The pipeline reads ``EmailSwitch`` alone, so the migration copies every
setting that is OFF into the ``ops.<kind>`` switch. A setting still on needs
no row (no row means on). These tests run its function over the state a
store is in: the settings, and no switches.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core import mail
from django.db import connection
from extra_settings.models import Setting

from emails.models import EmailSwitch
from emails.send import Status, send_email
from emails.services import set_switch
from shipping.emails import WebhookFailed
from tests.utils.staff import store_staff

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("tenant", "open_email_gates"),
]

migration = importlib.import_module(
    "emails.migrations.0003_import_ops_alert_switches"
)

#: The ten settings the alerts had and the type each became.
SETTINGS_TO_TYPES = {
    "OPS_ALERT_STALE_SHIPMENTS_ENABLED": "ops.stale_shipments",
    "OPS_ALERT_UNREMITTED_COD_ENABLED": "ops.unremitted_cod",
    "OPS_ALERT_UNATTRIBUTABLE_COD_PAYOUTS_ENABLED": (
        "ops.unattributable_cod_payouts"
    ),
    "OPS_ALERT_SHIPMENT_REJECTED_ENABLED": "ops.shipment_rejected",
    "OPS_ALERT_WEBHOOK_FAILED_ENABLED": "ops.webhook_failed",
    "OPS_ALERT_PAYMENT_AFTER_CANCEL_ENABLED": "ops.payment_after_cancel",
    "OPS_ALERT_REFUND_FAILED_ENABLED": "ops.refund_failed",
    "OPS_ALERT_REVIEW_PENDING_ENABLED": "ops.review_pending",
    "OPS_ALERT_GIFT_CARD_REVERSAL_ENABLED": "ops.gift_card_reversal",
    "OPS_ALERT_PAYMENT_MISMATCH_ENABLED": "ops.payment_mismatch",
}


def run_migration() -> None:
    migration.import_switches(apps, SimpleNamespace(connection=connection))


def setting(name: str, *, enabled: bool) -> None:
    Setting.objects.create(name=name, value_type="bool", value_bool=enabled)


def switched_off() -> set[str]:
    return set(EmailSwitch.objects.values_list("type_key", flat=True))


class TestTheImport:
    @pytest.mark.parametrize(("name", "key"), SETTINGS_TO_TYPES.items())
    def test_a_muted_setting_becomes_its_types_switch(self, name, key):
        setting(name, enabled=False)

        run_migration()

        assert switched_off() == {key}

    def test_every_setting_maps_to_a_registered_switchable_type(self):
        from emails.registry import registry

        for key in SETTINGS_TO_TYPES.values():
            assert registry.get(key).switchable

    def test_a_setting_still_on_gets_no_row(self):
        for name in SETTINGS_TO_TYPES:
            setting(name, enabled=True)

        run_migration()

        assert switched_off() == set()

    def test_only_the_muted_ones_are_imported(self):
        setting("OPS_ALERT_WEBHOOK_FAILED_ENABLED", enabled=False)
        setting("OPS_ALERT_REFUND_FAILED_ENABLED", enabled=True)
        setting("CONTACT_EMAIL_ENABLED", enabled=False)

        run_migration()

        assert switched_off() == {"ops.webhook_failed"}

    def test_it_can_run_twice(self):
        setting("OPS_ALERT_WEBHOOK_FAILED_ENABLED", enabled=False)

        run_migration()
        run_migration()

        assert EmailSwitch.objects.count() == 1

    def test_an_existing_switch_is_left_as_it_was(self, tenant):
        set_switch(
            store_staff(tenant, is_superuser=True),
            "ops.webhook_failed",
            enabled=False,
        )
        setting("OPS_ALERT_WEBHOOK_FAILED_ENABLED", enabled=False)
        row = EmailSwitch.objects.get()

        run_migration()

        assert EmailSwitch.objects.get() == row

    def test_the_stores_cached_switches_move_on(self, tenant):
        """The pipeline caches a store's switches per cache generation; a
        bulk insert fires no signal, so the migration moves it itself."""
        from emails.send import switched_off_keys

        assert switched_off_keys() == frozenset()
        setting("OPS_ALERT_WEBHOOK_FAILED_ENABLED", enabled=False)
        before = type(tenant).objects.get(pk=tenant.pk).cache_generation

        run_migration()

        tenant.refresh_from_db()
        assert tenant.cache_generation > before
        with patch("emails.send.bound_tenant", return_value=tenant):
            assert switched_off_keys() == {"ops.webhook_failed"}


class TestAfterTheImportOnlyTheSwitchDecides:
    def test_a_muted_store_still_sends_nothing(self):
        setting("OPS_ALERT_WEBHOOK_FAILED_ENABLED", enabled=False)
        run_migration()
        mail.outbox.clear()

        with patch(
            "emails.recipients.tenant_operator_addresses",
            return_value=["owner@store.test"],
        ):
            [delivery] = send_email(
                "ops.webhook_failed",
                WebhookFailed(carrier="BoxNow", message_id="m1", error="x"),
            )

        assert delivery.status is Status.SKIPPED_SWITCHED_OFF
        assert mail.outbox == []
