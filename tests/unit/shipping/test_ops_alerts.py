"""Operational alert emails: one gate, one switch per kind.

Every operational email goes through ``shipping.alerts.send_ops_alert``,
which asks the store whether it wants that kind (``OpsAlertKind``) before
it sends. A store opts OUT with ``OPS_ALERT_<KIND>_ENABLED`` (default on),
so a kind added later reaches every store. A muted email loses nothing:
the order note, the claim flag and the dashboard counter all stay.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.utils import timezone
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models.history import OrderHistory
from order.services import OrderService
from shipping.alerts import (
    OpsAlertKind,
    alert_admins_shipment_creation_failed,
    alert_admins_webhook_processing_failed,
    ops_alert_enabled,
    send_ops_alert,
)
from shipping_acs.enum.charge_type import AcsChargeType
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.services import AcsService
from shipping_acs.tasks import (
    alert_unremitted_cod_payouts,
    check_stale_acs_shipments,
)
from tests.utils.store_settings import switch_store_settings

pytestmark = pytest.mark.django_db

REPO = Path(settings.BASE_DIR)


@pytest.fixture(autouse=True)
def _operators(settings):
    settings.ADMINS = [("Ops", "ops@example.com")]


@pytest.fixture(autouse=True)
def _acs_configured():
    with patch("shipping_acs.config.is_configured", return_value=True):
        yield


@pytest.fixture
def mail():
    with patch("shipping.alerts.send_mail") as send_mail:
        yield send_mail


def _mute(*kinds: OpsAlertKind) -> None:
    switch_store_settings(**{kind.setting_name: False for kind in kinds})


class TestTheSettingPerKind:
    def test_seven_kinds_and_none_of_the_removed_ones(self):
        assert {kind.value for kind in OpsAlertKind} == {
            "stale_shipments",
            "unremitted_cod",
            "unattributable_cod_payouts",
            "shipment_rejected",
            "webhook_failed",
            "payment_after_cancel",
            "review_pending",
        }

    def test_every_kind_has_a_boolean_setting_that_defaults_on(self):
        seeded = {
            entry["name"]: entry
            for entry in settings.EXTRA_SETTINGS_DEFAULTS
            if entry["name"].startswith("OPS_ALERT_")
        }

        assert set(seeded) == {kind.setting_name for kind in OpsAlertKind}
        for entry in seeded.values():
            assert entry["type"] == "bool"
            assert entry["value"] is True
            assert entry["description"]

    def test_a_store_with_no_row_gets_every_kind(self):
        assert all(ops_alert_enabled(kind) for kind in OpsAlertKind)

    def test_muting_one_kind_leaves_the_others_on(self):
        _mute(OpsAlertKind.STALE_SHIPMENTS)

        assert not ops_alert_enabled(OpsAlertKind.STALE_SHIPMENTS)
        assert all(
            ops_alert_enabled(kind)
            for kind in OpsAlertKind
            if kind is not OpsAlertKind.STALE_SHIPMENTS
        )


class TestTheGate:
    @pytest.mark.parametrize("kind", list(OpsAlertKind))
    def test_an_enabled_kind_is_sent(self, kind, mail):
        assert send_ops_alert(kind=kind, subject="s", message="m") is True
        assert mail.call_count == 1

    @pytest.mark.parametrize("kind", list(OpsAlertKind))
    def test_a_muted_kind_is_not(self, kind, mail):
        _mute(kind)

        assert send_ops_alert(kind=kind, subject="s", message="m") is False
        mail.assert_not_called()

    def test_a_mute_is_only_for_its_own_kind(self, mail):
        _mute(OpsAlertKind.WEBHOOK_FAILED)

        assert send_ops_alert(
            kind=OpsAlertKind.SHIPMENT_REJECTED, subject="s", message="m"
        )
        mail.assert_called_once()

    def test_html_travels_with_the_message(self, mail):
        send_ops_alert(
            kind=OpsAlertKind.STALE_SHIPMENTS,
            subject="s",
            message="m",
            html_message="<p>m</p>",
        )

        assert mail.call_args.kwargs["html_message"] == "<p>m</p>"

    def test_no_recipient_is_not_sent(self, mail, settings):
        settings.ADMINS = []

        assert (
            send_ops_alert(
                kind=OpsAlertKind.WEBHOOK_FAILED, subject="s", message="m"
            )
            is False
        )
        mail.assert_not_called()


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


class TestEverySenderUsesTheGate:
    """Each real sender, run for real, honours its own switch."""

    def test_stale_digest(self, mail):
        shipment = _stale_shipment()
        _mute(OpsAlertKind.STALE_SHIPMENTS)

        result = check_stale_acs_shipments.run()

        assert result == {"alerted": 0, "reason": "muted"}
        mail.assert_not_called()
        # Nothing was claimed: the parcel is in the first digest after the
        # store turns the email back on.
        shipment.refresh_from_db()
        assert shipment.stale_alert_sent is False

        switch_store_settings(
            **{OpsAlertKind.STALE_SHIPMENTS.setting_name: True}
        )
        assert check_stale_acs_shipments.run()["alerted"] == 1
        assert mail.call_count == 1

    def test_unremitted_cod(self, mail):
        _delivered_cod_shipment()
        _mute(OpsAlertKind.UNREMITTED_COD)

        result = alert_unremitted_cod_payouts.run()

        assert result["unremitted"] == 1
        assert result["alerted"] == 0
        mail.assert_not_called()

    def test_unattributable_cod_payouts(self, mail):
        AcsService._alert_admins_unmatched_payouts(
            skipped=1, cod_payment_date=None
        )
        assert mail.call_count == 1

        _mute(OpsAlertKind.UNATTRIBUTABLE_COD_PAYOUTS)
        AcsService._alert_admins_unmatched_payouts(
            skipped=1, cod_payment_date=None
        )
        assert mail.call_count == 1

    def test_shipment_rejected_keeps_the_order_note(self, mail):
        order = OrderFactory(num_order_items=0)
        _mute(OpsAlertKind.SHIPMENT_REJECTED)

        alert_admins_shipment_creation_failed(
            order_id=order.id, carrier="ACS", error="bad", remedy="fix it"
        )

        mail.assert_not_called()
        assert any(
            "ACS shipment creation failed: bad" in history.new_value["note"]
            for history in OrderHistory.objects.filter(
                order=order, change_type="NOTE"
            )
        )

    def test_webhook_failed(self, mail):
        alert_admins_webhook_processing_failed(
            carrier="BoxNow", message_id="m1", error="boom"
        )
        assert mail.call_count == 1

        _mute(OpsAlertKind.WEBHOOK_FAILED)
        alert_admins_webhook_processing_failed(
            carrier="BoxNow", message_id="m2", error="boom"
        )
        assert mail.call_count == 1

    def test_payment_after_cancel_still_books_the_money(self, mail):
        order = OrderFactory(
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.CANCELED,
            num_order_items=0,
        )
        _mute(OpsAlertKind.PAYMENT_AFTER_CANCEL)

        OrderService.record_payment_after_cancel(
            order, payment_id="pi_late", payment_method="stripe"
        )

        mail.assert_not_called()
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.metadata["payment_after_cancel"]["payment_id"] == (
            "pi_late"
        )


def _modules_that_send_mail() -> list[str]:
    """Modules of the carrier and order code that import ``send_mail``."""
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


def test_only_the_gate_sends_operational_mail():
    """The stale digest called ``send_mail`` itself for months, so it
    could not be muted. Carrier code mails through ``send_ops_alert``."""
    assert _modules_that_send_mail() == ["shipping/alerts.py"]
