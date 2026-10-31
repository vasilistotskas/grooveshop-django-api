"""The operational alerts are email types: every one honours its switch.

Ten alerts (``shipping``, ``shipping_acs``, ``order``, ``giftcard`` and
``product`` ``emails`` modules) tell a store's staff that something needs a
person. They go through ``emails.send_email`` like every other mail, so a
store switches each off in ``EmailSwitch``, a store with nobody to write to
skips them (never the platform's addresses), and the carrier code sends
nothing itself. A switched-off alert loses nothing: the order note, the
claim flag and the dashboard counter all stay.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core import mail
from django.utils import timezone
from djmoney.money import Money

from emails.registry import registry
from emails.send import Status, send_email
from emails.services import set_switch
from emails.types import Audience, Owner
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models.history import OrderHistory
from order.services import OrderService
from shipping.alerts import (
    alert_admins_shipment_creation_failed,
    alert_admins_webhook_processing_failed,
)
from shipping_acs.enum.charge_type import AcsChargeType
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.services import AcsService
from shipping_acs.tasks import (
    alert_unremitted_cod_payouts,
    check_stale_acs_shipments,
)
from tests.utils.staff import store_staff

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("tenant", "open_email_gates"),
]

REPO = Path(settings.BASE_DIR)
OPERATOR = "owner@store.test"

#: What ``OPS_ALERT_<KIND>_ENABLED`` became, one for one.
OPS_KEYS = {
    "ops.stale_shipments",
    "ops.unremitted_cod",
    "ops.unattributable_cod_payouts",
    "ops.shipment_rejected",
    "ops.webhook_failed",
    "ops.payment_after_cancel",
    "ops.refund_failed",
    "ops.review_pending",
    "ops.gift_card_reversal",
    "ops.payment_mismatch",
}


@pytest.fixture(autouse=True)
def _operators():
    mail.outbox.clear()
    with (
        patch(
            "emails.recipients.tenant_operator_addresses",
            return_value=[OPERATOR],
        ),
        patch("shipping_acs.config.is_configured", return_value=True),
    ):
        yield


def switch(tenant, key: str, *, enabled: bool) -> None:
    """Change a switch the way a store does, then start the test's own
    count of mail: what the setup sent (every order mails the store) and the
    store's cached switches, which follow the cache generation the write
    moved, are no longer in the way."""
    set_switch(store_staff(tenant, is_superuser=True), key, enabled=enabled)
    tenant.refresh_from_db()
    mail.outbox.clear()


def switch_off(tenant, key: str) -> None:
    switch(tenant, key, enabled=False)


class TestTheCatalogue:
    def test_the_ten_alerts_are_registered(self):
        keys = {t.key for t in registry.all_types() if t.key.startswith("ops.")}

        assert keys == OPS_KEYS

    @pytest.mark.parametrize("key", sorted(OPS_KEYS))
    def test_each_goes_to_the_stores_staff_and_can_be_switched_off(self, key):
        email_type = registry.get(key)

        assert email_type.audience is Audience.STORE_STAFF
        assert email_type.owner is Owner.STORE
        assert email_type.area == "ops"
        assert email_type.switchable

    def test_the_old_settings_are_gone(self):
        declared = {entry["name"] for entry in settings.EXTRA_SETTINGS_DEFAULTS}

        assert not {name for name in declared if name.startswith("OPS_ALERT_")}


class TestNoOneToWriteTo:
    def test_a_store_with_no_address_skips_the_alert_not_the_platform(
        self, settings
    ):
        settings.ADMINS = ["platform@example.test"]

        with patch(
            "emails.recipients.tenant_operator_addresses", return_value=[]
        ):
            [delivery] = send_email(
                "ops.webhook_failed",
                _webhook_failure(),
            )

        assert delivery.status is Status.SKIPPED_NO_RECIPIENT
        assert mail.outbox == []


def _webhook_failure():
    from shipping.emails import WebhookFailed

    return WebhookFailed(carrier="BoxNow", message_id="m1", error="boom")


def _stale_shipment():
    return AcsShipmentFactory(
        voucher_no="9700000001",
        shipment_state=AcsShipmentState.NEW,
        last_event_at=timezone.now() - timedelta(days=5),
    )


def _delivered_cod_shipment():
    return AcsShipmentFactory(
        order=OrderFactory(
            status=OrderStatus.DELIVERED, payment_status=PaymentStatus.PENDING
        ),
        voucher_no="9772892603",
        shipment_state=AcsShipmentState.DELIVERED,
        charge_type=AcsChargeType.COD,
        cod_amount=Money(Decimal("24.48"), "EUR"),
        delivery_date=timezone.now() - timedelta(days=30),
    )


class TestEverySenderUsesItsSwitch:
    """Each real sender, run for real, honours its own switch."""

    def test_stale_digest(self, tenant):
        shipment = _stale_shipment()
        switch_off(tenant, "ops.stale_shipments")

        result = check_stale_acs_shipments.run()

        assert result == {"alerted": 0, "reason": "skipped_switched_off"}
        assert mail.outbox == []
        # The claim is released: the parcel is in the first digest after the
        # store turns the email back on.
        shipment.refresh_from_db()
        assert shipment.stale_alert_sent is False

        switch(tenant, "ops.stale_shipments", enabled=True)
        assert check_stale_acs_shipments.run()["alerted"] == 1
        assert len(mail.outbox) == 1

    def test_unremitted_cod(self, tenant):
        _delivered_cod_shipment()
        switch_off(tenant, "ops.unremitted_cod")

        result = alert_unremitted_cod_payouts.run()

        assert result["unremitted"] == 1
        assert result["alerted"] == 0
        assert mail.outbox == []

    def test_unattributable_cod_payouts(self, tenant):
        AcsService._alert_admins_unmatched_payouts(
            skipped=1, cod_payment_date=None
        )
        assert len(mail.outbox) == 1

        switch_off(tenant, "ops.unattributable_cod_payouts")
        AcsService._alert_admins_unmatched_payouts(
            skipped=1, cod_payment_date=None
        )
        assert mail.outbox == []

    def test_shipment_rejected_keeps_the_order_note(self, tenant):
        order = OrderFactory(num_order_items=0)
        switch_off(tenant, "ops.shipment_rejected")

        alert_admins_shipment_creation_failed(
            order_id=order.id, carrier="ACS", error="bad", remedy="fix it"
        )

        assert mail.outbox == []
        assert any(
            "ACS shipment creation failed: bad" in history.new_value["note"]
            for history in OrderHistory.objects.filter(
                order=order, change_type="NOTE"
            )
        )

    def test_a_switch_is_only_for_its_own_alert(self, tenant):
        order = OrderFactory(num_order_items=0)
        switch_off(tenant, "ops.webhook_failed")

        alert_admins_shipment_creation_failed(
            order_id=order.id,
            carrier="ACS",
            error="bad",
            remedy="fix it",
        )

        assert len(mail.outbox) == 1

    def test_webhook_failed(self, tenant):
        alert_admins_webhook_processing_failed(
            carrier="BoxNow", message_id="m1", error="boom"
        )
        assert len(mail.outbox) == 1

        switch_off(tenant, "ops.webhook_failed")
        alert_admins_webhook_processing_failed(
            carrier="BoxNow", message_id="m2", error="boom"
        )
        assert mail.outbox == []

    def test_payment_after_cancel_still_books_the_money(self, tenant):
        order = OrderFactory(
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.CANCELED,
            num_order_items=0,
        )
        switch_off(tenant, "ops.payment_after_cancel")

        OrderService.record_payment_after_cancel(
            order, payment_id="pi_late", payment_method="stripe"
        )

        assert mail.outbox == []
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.metadata["payment_after_cancel"]["payment_id"] == (
            "pi_late"
        )


def _modules_that_send_mail() -> list[str]:
    """Modules of the carrier code that import ``send_mail``."""
    found = []
    for package in ("shipping", "shipping_acs", "shipping_boxnow"):
        for path in (REPO / package).rglob("*.py"):
            if "migrations" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and any(
                    alias.name == "send_mail" for alias in node.names
                ):
                    found.append(path.relative_to(REPO).as_posix())
    return found


def test_carrier_code_sends_no_mail_itself():
    """The stale digest called ``send_mail`` itself for months, so it could
    not be muted. Carrier code mails through ``emails.send_email`` only."""
    assert _modules_that_send_mail() == []
