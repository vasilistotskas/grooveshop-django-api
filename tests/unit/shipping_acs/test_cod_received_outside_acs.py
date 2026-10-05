"""Settling a COD parcel the merchant was paid for outside ACS.

Production order 73 (voucher 9772892603, EUR 24.48, delivered
2026-05-23): ACS never reported a payout on any date, the merchant
confirmed they were paid, and an admin had already marked the payment
COMPLETED by hand. The unremitted-COD alert therefore fired every night
with no way to clear it. ``record_cod_received_outside_acs`` is that way
out: an audited stamp on the shipment, never a forged ``AcsCodPayout``.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.services import OrderService
from shipping_acs.admin import AcsShipmentAdmin
from shipping_acs.enum.charge_type import AcsChargeType
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import AcsCodSettlementError
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsCodPayout, AcsShipment
from shipping_acs.services import AcsService
from shipping_acs.tasks import (
    _unremitted_cod_rows,
    alert_unremitted_cod_payouts,
)
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db

VOUCHER = "9772892603"


@pytest.fixture(autouse=True)
def _acs_configured():
    with patch("shipping_acs.config.is_configured", return_value=True):
        yield


@pytest.fixture
def staff_user():
    return UserAccountFactory(is_staff=True, is_superuser=True)


def _shipment(
    *,
    payment_status=PaymentStatus.PENDING,
    state=AcsShipmentState.DELIVERED,
    charge_type=AcsChargeType.COD,
    amount="24.48",
    voucher=VOUCHER,
    days_ago=30,
):
    order = OrderFactory(
        status=OrderStatus.DELIVERED, payment_status=payment_status
    )
    return AcsShipmentFactory(
        order=order,
        voucher_no=voucher,
        shipment_state=state,
        charge_type=charge_type,
        cod_amount=Money(Decimal(amount), "EUR"),
        delivery_date=timezone.now() - timedelta(days=days_ago),
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def test_recording_stamps_the_shipment_and_pays_the_order_silently(
    staff_user,
):
    shipment = _shipment()

    with patch.object(OrderService, "maybe_advance_to_completed") as advance:
        AcsService.record_cod_received_outside_acs(
            shipment, user=staff_user, note="Bank transfer 2026-05-30"
        )

    shipment.refresh_from_db()
    assert shipment.cod_received_outside_acs_at is not None
    assert shipment.cod_received_outside_acs_by_id == staff_user.pk
    assert shipment.cod_received_outside_acs_note == "Bank transfer 2026-05-30"
    shipment.order.refresh_from_db()
    assert shipment.order.payment_status == PaymentStatus.COMPLETED
    # The shopper paid in person and was already told it was delivered.
    assert advance.call_args.kwargs["silent_for_customer"] is True
    # ACS's own statement is never forged.
    assert not AcsCodPayout.objects.exists()


def test_recording_is_a_noop_for_an_order_already_marked_paid(staff_user):
    """Order 73: an admin had already flipped the payment by hand."""
    shipment = _shipment(payment_status=PaymentStatus.COMPLETED)

    with patch.object(OrderService, "maybe_advance_to_completed") as advance:
        AcsService.record_cod_received_outside_acs(
            shipment, user=staff_user, note="Paid in cash at the shop"
        )

    shipment.refresh_from_db()
    assert shipment.cod_received_outside_acs_at is not None
    shipment.order.refresh_from_db()
    assert shipment.order.payment_status == PaymentStatus.COMPLETED
    assert not advance.called


@pytest.mark.parametrize(
    "kwargs",
    [
        {"charge_type": AcsChargeType.PREPAID},
        {"amount": "0"},
        {"state": AcsShipmentState.IN_TRANSIT},
    ],
    ids=["not-cod", "nothing-due", "not-delivered"],
)
def test_refuses_a_parcel_that_is_not_a_delivered_cod_with_an_amount(
    staff_user, kwargs
):
    shipment = _shipment(**kwargs)

    with pytest.raises(AcsCodSettlementError):
        AcsService.record_cod_received_outside_acs(
            shipment, user=staff_user, note="x"
        )

    shipment.refresh_from_db()
    assert shipment.cod_received_outside_acs_at is None
    shipment.order.refresh_from_db()
    assert shipment.order.payment_status == PaymentStatus.PENDING


def test_refuses_when_acs_already_reported_a_payout(staff_user):
    shipment = _shipment()
    AcsCodPayout.objects.create(
        voucher_no=VOUCHER,
        cod_payment_date=timezone.localdate(),
        cod_amount_total=Money(Decimal("24.48"), "EUR"),
    )

    with pytest.raises(AcsCodSettlementError, match="payout"):
        AcsService.record_cod_received_outside_acs(
            shipment, user=staff_user, note="x"
        )

    shipment.refresh_from_db()
    assert shipment.cod_received_outside_acs_at is None


def test_refuses_a_second_recording_and_keeps_the_first(staff_user):
    shipment = _shipment()
    AcsService.record_cod_received_outside_acs(
        shipment, user=staff_user, note="first"
    )

    # A double submit hands in the stale, pre-stamp instance.
    with pytest.raises(AcsCodSettlementError, match="already recorded"):
        AcsService.record_cod_received_outside_acs(
            shipment, user=staff_user, note="second"
        )

    shipment.refresh_from_db()
    assert shipment.cod_received_outside_acs_note == "first"


def test_refuses_a_blank_note(staff_user):
    shipment = _shipment()

    with pytest.raises(AcsCodSettlementError, match="note"):
        AcsService.record_cod_received_outside_acs(
            shipment, user=staff_user, note="   "
        )


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


def test_the_watcher_skips_a_settled_shipment_and_still_reports_others(
    staff_user,
):
    settled = _shipment(voucher=VOUCHER)
    _shipment(voucher="9775922531", amount="24.97")
    assert len(_unremitted_cod_rows(older_than_days=10)) == 2

    AcsService.record_cod_received_outside_acs(
        settled, user=staff_user, note="Bank transfer"
    )

    rows = _unremitted_cod_rows(older_than_days=10)
    assert [r["voucher_no"] for r in rows] == ["9775922531"]


def test_the_alert_names_the_action_that_clears_a_parcel():
    _shipment()

    with patch("shipping.alerts.send_ops_alert", return_value=True) as alert:
        alert_unremitted_cod_payouts()

    message = alert.call_args.kwargs["message"]
    assert "Record COD received outside ACS" in message


# ---------------------------------------------------------------------------
# Admin action
# ---------------------------------------------------------------------------


@pytest.fixture
def shipment_admin():
    return AcsShipmentAdmin(AcsShipment, AdminSite())


def _request(staff_user, method="post", data=None):
    """A real request: unfold's dialog wrapper builds its own form from
    ``request.POST`` and only runs the body once it validates, so a stub
    form would leave the action unexecuted (see test_b2b_admin)."""
    factory = RequestFactory()
    path = "/admin/shipping_acs/acsshipment/"
    request = (
        factory.post(path, {"_form_submitted": "true", **(data or {})})
        if method == "post"
        else factory.get(path)
    )
    request.user = staff_user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _redirect(shipment):
    return reverse("admin:shipping_acs_acsshipment_change", args=[shipment.pk])


def test_admin_get_shows_the_note_form(shipment_admin, staff_user):
    shipment = _shipment()

    response = shipment_admin.record_cod_received_outside_acs(
        _request(staff_user, "get"), object_id=shipment.pk
    )

    assert response.status_code == 200
    assert 'name="note"' in response.content.decode()
    shipment.refresh_from_db()
    assert shipment.cod_received_outside_acs_at is None


def test_admin_post_records_and_redirects(shipment_admin, staff_user):
    shipment = _shipment()

    response = shipment_admin.record_cod_received_outside_acs(
        _request(staff_user, data={"note": "Bank transfer"}),
        object_id=shipment.pk,
    )

    shipment.refresh_from_db()
    assert shipment.cod_received_outside_acs_at is not None
    assert shipment.cod_received_outside_acs_by_id == staff_user.pk
    assert shipment.cod_received_outside_acs_note == "Bank transfer"
    assert response.headers["HX-Redirect"] == _redirect(shipment)


def test_admin_post_requires_a_note(shipment_admin, staff_user):
    shipment = _shipment()

    shipment_admin.record_cod_received_outside_acs(
        _request(staff_user), object_id=shipment.pk
    )

    shipment.refresh_from_db()
    assert shipment.cod_received_outside_acs_at is None


def test_admin_post_reports_a_refusal_instead_of_recording(
    shipment_admin, staff_user
):
    shipment = _shipment(state=AcsShipmentState.IN_TRANSIT)
    request = _request(staff_user, data={"note": "x"})

    response = shipment_admin.record_cod_received_outside_acs(
        request, object_id=shipment.pk
    )

    shipment.refresh_from_db()
    assert shipment.cod_received_outside_acs_at is None
    assert response.headers["HX-Redirect"] == _redirect(shipment)
    assert "delivered" in str(next(iter(request._messages)))
