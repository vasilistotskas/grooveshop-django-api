"""COD money that goes missing must announce itself.

Three blind spots found in production on 2026-09-08, all in the same
place — the moment a parcel reaches a terminal state:

* ``poll_acs_tracking`` and ``check_stale_acs_shipments`` both EXCLUDE
  terminal shipment states, and a COD parcel that is delivered but never
  remitted is precisely a delivered parcel. Order 73 sat delivered and
  unpaid for 108 days (EUR 24.48, no payout row on any date ACS reports)
  with no alert able to reach it.
* ``reconcile_cod_payouts`` alerted only on rows whose voucher could not
  be READ. A row carrying a legible but unknown voucher was upserted,
  counted a success, and never mentioned — two of them, EUR 49.46, both
  on our own Customer_Code.
* The order-status transition is attempted exactly once, when the
  shipment turns terminal. Nine orders whose parcel came back months
  earlier were still in PROCESSING because that single attempt predated
  the SHIPPED-bridging fix and the poll never revisits them.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from shipping_acs.enum.charge_type import AcsChargeType
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsCodPayout
from shipping_acs.services import AcsService
from shipping_acs.tasks import (
    _unremitted_cod_rows,
    alert_unremitted_cod_payouts,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _acs_configured():
    """The test tenant carries no ACS credentials.

    Both the task and the reconcile guard on ``is_configured()`` so an
    unconfigured tenant is skipped rather than raising — correct in
    production, but it would make every test here a silent no-op.
    """
    with patch("shipping_acs.config.is_configured", return_value=True):
        yield


def _run_replay(**options):
    """Invoke the replay against the caller's own connection context.

    ``[None]`` is what the mixin yields with no ``--tenant`` flag, i.e.
    the context a fanout has already entered. Patched rather than passed
    because ``require_tenant_scope`` rejects the public schema and the
    plain ``tests/`` lane deliberately runs there.
    """
    from shipping_acs.management.commands import reconcile_acs_order_status

    with patch.object(
        reconcile_acs_order_status.Command,
        "get_target_tenants",
        return_value=[None],
    ):
        call_command("reconcile_acs_order_status", **options)


def _cod_shipment(*, days_ago: int, voucher: str, state=None, amount="24.48"):
    order = OrderFactory(
        status=OrderStatus.DELIVERED, payment_status=PaymentStatus.PENDING
    )
    return AcsShipmentFactory(
        order=order,
        voucher_no=voucher,
        shipment_state=state or AcsShipmentState.DELIVERED,
        charge_type=AcsChargeType.COD,
        cod_amount=Money(Decimal(amount), "EUR"),
        delivery_date=timezone.now() - timedelta(days=days_ago),
    )


# ---------------------------------------------------------------------------
# Delivered but never remitted
# ---------------------------------------------------------------------------


def test_a_long_delivered_cod_parcel_with_no_payout_is_reported():
    shipment = _cod_shipment(days_ago=30, voucher="9772892603")

    rows = _unremitted_cod_rows(older_than_days=10)

    assert [r["voucher_no"] for r in rows] == ["9772892603"]
    row = rows[0]
    assert row["order_id"] == shipment.order_id
    assert row["days_since_delivery"] >= 30
    # The alert is only actionable with the number to quote at ACS.
    assert row["cod_amount"].startswith("24")


def test_a_remitted_parcel_is_not_reported():
    shipment = _cod_shipment(days_ago=30, voucher="9772892603")
    AcsCodPayout.objects.create(
        voucher_no=shipment.voucher_no,
        cod_payment_date=timezone.localdate(),
        cod_amount_total=Money(Decimal("24.48"), "EUR"),
    )

    assert _unremitted_cod_rows(older_than_days=10) == []


def test_a_parcel_inside_the_normal_lag_is_not_reported():
    """The measured ACS lag is ~4 days; 10 must not fire on day 3."""
    _cod_shipment(days_ago=3, voucher="9803445971")

    assert _unremitted_cod_rows(older_than_days=10) == []


def test_a_prepaid_parcel_is_not_reported():
    """No COD was ever due, so there is nothing to remit.

    ``cod_amount`` carries a non-zero value on purpose: the model's own
    help text says the amount is "required when charge_type = COD;
    ignored otherwise", so a prepaid shipment can legitimately hold a
    stale figure. Testing this with a zero amount would let the
    ``cod_amount__gt=0`` clause pass for the ``charge_type`` one and the
    COD filter could be deleted with every test still green — which is
    exactly what the mutation run showed.
    """
    order = OrderFactory(status=OrderStatus.DELIVERED)
    AcsShipmentFactory(
        order=order,
        voucher_no="9800000001",
        shipment_state=AcsShipmentState.DELIVERED,
        charge_type=AcsChargeType.PREPAID,
        cod_amount=Money(Decimal("31.50"), "EUR"),
        delivery_date=timezone.now() - timedelta(days=40),
    )

    assert _unremitted_cod_rows(older_than_days=10) == []


def test_a_cod_parcel_with_nothing_left_to_collect_is_not_reported():
    """A zero COD amount means the courier had nothing to hand over."""
    order = OrderFactory(status=OrderStatus.DELIVERED)
    AcsShipmentFactory(
        order=order,
        voucher_no="9800000004",
        shipment_state=AcsShipmentState.DELIVERED,
        charge_type=AcsChargeType.COD,
        cod_amount=Money(Decimal(0), "EUR"),
        delivery_date=timezone.now() - timedelta(days=40),
    )

    assert _unremitted_cod_rows(older_than_days=10) == []


def test_an_undelivered_parcel_is_not_reported():
    """Money is not owed until the courier has collected it."""
    _cod_shipment(
        days_ago=40, voucher="9800000002", state=AcsShipmentState.IN_TRANSIT
    )

    assert _unremitted_cod_rows(older_than_days=10) == []


def test_the_task_alerts_and_names_every_parcel():
    _cod_shipment(days_ago=30, voucher="9772892603")
    _cod_shipment(days_ago=20, voucher="9775922531", amount="24.97")

    with patch("shipping.alerts.send_ops_alert", return_value=True) as alert:
        result = alert_unremitted_cod_payouts()

    assert result["unremitted"] == 2
    body = alert.call_args.kwargs["message"]
    assert "9772892603" in body
    assert "9775922531" in body
    # An operator needs the replay recipe, not just the bad news.
    assert "reconcile_acs_cod" in body


def test_the_task_says_nothing_when_everything_is_remitted():
    with patch("shipping.alerts.send_ops_alert") as alert:
        result = alert_unremitted_cod_payouts()

    assert result == {"status": "ok", "unremitted": 0}
    assert not alert.called


def test_a_mail_failure_does_not_lose_the_finding():
    """The log line is the durable record; SMTP is best-effort."""
    _cod_shipment(days_ago=30, voucher="9772892603")

    with (
        patch(
            "shipping.alerts.send_ops_alert", side_effect=RuntimeError("smtp")
        ),
        patch("shipping_acs.tasks.logger") as log,
    ):
        result = alert_unremitted_cod_payouts()

    assert result["unremitted"] == 1
    assert result["alerted"] == 0
    assert log.warning.called, "the parcels were never logged"


# ---------------------------------------------------------------------------
# Unattributable payouts
# ---------------------------------------------------------------------------


_ROW = {
    "Customer_Code": "2AK89587",
    "POD": "2431665832",
    "Parcel_Sender": "WEBSIDE.GR",
    "Parcel_Receiver": "Someone",
    "Parcel_Pickup_Date": "2026-08-14T00:00:00",
    "Parcel_Delivery_Date": "2026-08-18T00:00:00",
    "Parcel_COD_Amount": 24.97,
    "Customer_RefNo_1": None,
    "Customer_RefNo_2": None,
    "COD_Amount_Cach": 24.97,
    "COD_Amount_CreditCard": 0.0,
}


def test_a_payout_for_an_unknown_voucher_alerts():
    """It used to be counted a success and never mentioned."""
    with (
        patch("shipping_acs.services.AcsClient") as client,
        patch(
            "shipping_acs.services.AcsService._alert_admins_unmatched_payouts"
        ) as alert,
    ):
        client.return_value.cod_beneficiary_info.return_value = [_ROW]
        result = AcsService.reconcile_cod_payouts()

    assert result["unmatched"] == 1
    assert result["linked"] == 0
    assert alert.call_args.kwargs["unmatched"] == 1


def test_the_unknown_voucher_row_is_still_persisted():
    """It is real money — dropping the only record of it is worse."""
    with patch("shipping_acs.services.AcsClient") as client:
        client.return_value.cod_beneficiary_info.return_value = [_ROW]
        AcsService.reconcile_cod_payouts()

    payout = AcsCodPayout.objects.get(voucher_no="2431665832")
    assert payout.shipment_id is None
    assert payout.raw_payload["Parcel_Receiver"] == "Someone"


def test_the_warning_carries_what_identifies_the_row():
    with (
        patch("shipping_acs.services.AcsClient") as client,
        patch("shipping_acs.services.logger") as log,
    ):
        client.return_value.cod_beneficiary_info.return_value = [_ROW]
        AcsService.reconcile_cod_payouts()

    logged = " ".join(str(c) for c in log.warning.call_args_list)
    for needle in ("2431665832", "2AK89587", "24.97", "WEBSIDE.GR"):
        assert needle in logged, f"{needle} missing from the warning"


def test_a_matched_payout_alerts_nobody():
    order = OrderFactory(payment_status=PaymentStatus.PENDING)
    AcsShipmentFactory(order=order, voucher_no="2431665832")

    with (
        patch("shipping_acs.services.AcsClient") as client,
        patch(
            "shipping_acs.services.AcsService._alert_admins_unmatched_payouts"
        ) as alert,
    ):
        client.return_value.cod_beneficiary_info.return_value = [_ROW]
        result = AcsService.reconcile_cod_payouts()

    assert result["unmatched"] == 0
    assert result["linked"] == 1
    assert not alert.called


# ---------------------------------------------------------------------------
# Replaying a stranded order status
# ---------------------------------------------------------------------------


def test_the_mapping_is_asked_not_re_derived():
    """One function answers for both the poll and the replay."""
    assert (
        AcsService.order_status_for_shipment_state(
            AcsShipmentState.RETURNED, "PROCESSING"
        )
        == "RETURNED"
    )
    assert (
        AcsService.order_status_for_shipment_state(
            AcsShipmentState.DELIVERED, "SHIPPED"
        )
        == "DELIVERED"
    )
    # Already agreed, or already terminal — leave it alone.
    assert (
        AcsService.order_status_for_shipment_state(
            AcsShipmentState.RETURNED, "RETURNED"
        )
        is None
    )
    assert (
        AcsService.order_status_for_shipment_state(
            AcsShipmentState.DELIVERED, "CANCELED"
        )
        is None
    )


def _stranded_order():
    order = OrderFactory(
        status=OrderStatus.PROCESSING, payment_status=PaymentStatus.PENDING
    )
    AcsShipmentFactory(
        order=order,
        voucher_no="9771670285",
        shipment_state=AcsShipmentState.RETURNED,
        charge_type=AcsChargeType.COD,
        cod_amount=Money(Decimal("24.49"), "EUR"),
    )
    return order


def test_the_replay_reports_without_writing_by_default(capsys):
    order = _stranded_order()

    _run_replay(orders=[order.id])

    order.refresh_from_db()
    assert order.status == OrderStatus.PROCESSING, "a dry run wrote"
    out = capsys.readouterr().out
    assert f"order {order.id}: PROCESSING -> RETURNED" in out
    assert "Dry run" in out


def test_apply_bridges_processing_through_shipped_to_returned(capsys):
    order = _stranded_order()

    _run_replay(orders=[order.id], apply=True)

    order.refresh_from_db()
    # The state machine forbids PROCESSING -> RETURNED; the shared
    # transition walks the missing SHIPPED step first.
    assert order.status == OrderStatus.RETURNED
    assert "-> RETURNED" in capsys.readouterr().out


def test_the_replay_never_emails_the_customer():
    """These transitions are months late — silence beats confusion."""
    order = _stranded_order()

    with patch(
        "shipping_acs.services.AcsService._apply_order_status_transition"
    ) as transition:
        _run_replay(orders=[order.id], apply=True)

    assert transition.call_args.kwargs["silent_for_customer"] is True


def test_an_agreeing_order_is_left_alone(capsys):
    order = OrderFactory(status=OrderStatus.DELIVERED)
    AcsShipmentFactory(
        order=order,
        voucher_no="9800000003",
        shipment_state=AcsShipmentState.DELIVERED,
    )

    _run_replay(orders=[order.id])

    assert "agrees with its order status" in capsys.readouterr().out
