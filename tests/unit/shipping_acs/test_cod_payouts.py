"""Phase 4c — AcsService.reconcile_cod_payouts upsert behaviour.

The row fixture mirrors the REAL ``ACS_COD_Beneficiary_Info``
``Table_Data`` schema from the ACS PDF: the voucher number travels in
``POD`` and there is **no** ``Voucher_No`` column. The original fixture
invented a ``Voucher_No`` key, so the tests passed while production
skipped every payout row for 10 weeks (discovered 2026-07-11) — do not
add wire keys here that the PDF does not document.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from order.factories.order import OrderFactory
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsCodPayout
from shipping_acs.services import AcsService

pytestmark = pytest.mark.django_db


_BASE_ROW = {
    "Customer_Code": "2",
    "POD": "7227891234",
    "Parcel_Sender": "GrooveShop",
    "Parcel_Receiver": "Customer",
    "Parcel_Pickup_Date": "2026-04-26T00:00:00",
    "Parcel_Delivery_Date": "2026-04-28T00:00:00",
    "Parcel_COD_Amount": 120.30,
    "Customer_RefNo_1": "1234",
    "Customer_RefNo_2": "",
    "COD_Amount_Cach": 0.00,
    "COD_Amount_CreditCard": 120.30,
}


def _reconcile(rows, **kwargs):
    with patch("shipping_acs.services.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.cod_beneficiary_info.return_value = rows
        return AcsService.reconcile_cod_payouts(**kwargs)


def test_reconcile_matches_shipment_by_pod_voucher():
    from order.enum.status import OrderStatus, PaymentStatus

    order = OrderFactory(
        status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
    )
    shipment = AcsShipmentFactory(order=order, voucher_no="7227891234")

    result = _reconcile([_BASE_ROW])

    assert result == {"upserted": 1, "linked": 1, "skipped": 0, "rows": 1}
    payout = AcsCodPayout.objects.get(voucher_no="7227891234")
    assert payout.shipment_id == shipment.id
    assert payout.cod_amount_total.amount == Decimal("120.30")
    assert payout.customer_ref_no_1 == "1234"


def test_reconcile_falls_back_to_customer_ref_order_id():
    """When ``POD`` is empty (seen in the PDF demo response), the
    ``Customer_RefNo_1`` echo of ``Reference_Key1`` (= order.id)
    resolves the shipment, and the payout row inherits the shipment's
    voucher number."""
    from order.enum.status import OrderStatus, PaymentStatus

    order = OrderFactory(
        status=OrderStatus.PROCESSING, payment_status=PaymentStatus.PENDING
    )
    shipment = AcsShipmentFactory(order=order, voucher_no="7227895678")

    row = {**_BASE_ROW, "POD": "", "Customer_RefNo_1": str(order.id)}
    result = _reconcile([row])

    assert result == {"upserted": 1, "linked": 1, "skipped": 0, "rows": 1}
    payout = AcsCodPayout.objects.get(voucher_no="7227895678")
    assert payout.shipment_id == shipment.id
    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.COMPLETED


def test_reconcile_falls_back_to_customer_ref_order_uuid():
    """``Customer_RefNo_2`` echoes ``Reference_Key2`` (= order.uuid)."""
    from order.enum.status import OrderStatus, PaymentStatus

    order = OrderFactory(
        status=OrderStatus.PROCESSING, payment_status=PaymentStatus.PENDING
    )
    shipment = AcsShipmentFactory(order=order, voucher_no="7227899999")

    row = {
        **_BASE_ROW,
        "POD": "",
        "Customer_RefNo_1": "",
        "Customer_RefNo_2": str(order.uuid),
    }
    result = _reconcile([row])

    assert result["linked"] == 1
    assert AcsCodPayout.objects.get(voucher_no="7227899999").shipment_id == (
        shipment.id
    )


def test_reconcile_creates_orphan_row_when_shipment_missing():
    """ACS reports parcels we don't have AcsShipment rows for —
    those still need to be persisted, just without a FK link."""
    result = _reconcile([{**_BASE_ROW, "POD": "9999000111"}])

    assert result == {"upserted": 1, "linked": 0, "skipped": 0, "rows": 1}
    payout = AcsCodPayout.objects.get(voucher_no="9999000111")
    assert payout.shipment_id is None


def test_reconcile_is_idempotent_on_voucher_date_pair():
    """Re-running the task with the same row updates rather than
    creates a duplicate — the unique constraint enforces it but
    update_or_create makes the path graceful."""
    AcsShipmentFactory(voucher_no="7227891234")

    _reconcile([_BASE_ROW], cod_payment_date=date(2026, 4, 29))
    _reconcile(
        [{**_BASE_ROW, "Parcel_COD_Amount": 200.00}],
        cod_payment_date=date(2026, 4, 29),
    )

    rows = AcsCodPayout.objects.filter(voucher_no="7227891234")
    assert rows.count() == 1
    assert rows.get().cod_amount_total.amount == Decimal("200.00")


def test_reconcile_skips_and_alerts_on_unmatched_rows(settings):
    """A row with no POD and no resolvable reference is counted as
    skipped and ADMINS are alerted — unmatched payout money must be
    investigated, not silently dropped."""
    settings.ADMINS = ["admin@example.com"]

    with patch("django.core.mail.mail_admins") as mock_mail:
        result = _reconcile(
            [_BASE_ROW, {"Customer_Code": "x", "POD": ""}],
        )

    assert result["upserted"] == 1
    assert result["skipped"] == 1
    assert result["rows"] == 2
    assert mock_mail.called


def test_reconcile_fires_order_paid_for_cod_purchase_capi():
    """COD reconcile must emit ``order_paid`` so Meta CAPI Purchase
    dispatches.

    Regression: the post_save chain only fires ``order_paid`` on a
    PENDING → PROCESSING status transition with ``is_paid=True``.
    COD orders are already at PROCESSING (status advanced at
    voucher-mint), so the COD payment confirmation never reached
    that branch and Meta saw zero Purchase events for COD revenue.
    """
    from order.enum.status import OrderStatus, PaymentStatus
    from order.signals import order_paid

    order = OrderFactory(
        status=OrderStatus.PROCESSING, payment_status=PaymentStatus.PENDING
    )
    AcsShipmentFactory(order=order, voucher_no="7227891234")

    received: list[dict] = []

    def _listener(sender, order, **kwargs):
        received.append({"order_id": order.id})

    order_paid.connect(_listener, dispatch_uid="test.cod_order_paid")
    try:
        _reconcile([_BASE_ROW])
    finally:
        order_paid.disconnect(dispatch_uid="test.cod_order_paid")

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.COMPLETED
    assert received == [{"order_id": order.id}]


def test_reconcile_does_not_refire_order_paid_when_already_paid():
    """Idempotency: a payout row re-uploaded by ACS must not re-fire
    ``order_paid``. ``_mark_cod_order_paid_if_pending`` gates on
    ``payment_status == PENDING``; once flipped, subsequent payout
    upserts are no-ops on the signal channel even if the row is
    upserted again.
    """
    from order.enum.status import OrderStatus, PaymentStatus
    from order.signals import order_paid

    order = OrderFactory(
        status=OrderStatus.PROCESSING, payment_status=PaymentStatus.COMPLETED
    )
    AcsShipmentFactory(order=order, voucher_no="7227891234")

    fired: list[int] = []

    def _listener(sender, order, **kwargs):
        fired.append(order.id)

    order_paid.connect(_listener, dispatch_uid="test.cod_no_refire")
    try:
        _reconcile([_BASE_ROW])
    finally:
        order_paid.disconnect(dispatch_uid="test.cod_no_refire")

    assert fired == []


def test_reconcile_silent_mode_suppresses_completed_notification():
    """``silent_for_customer=True`` (backfill path) pre-stamps the
    COMPLETED suppression flags so a weeks-old order flipping to paid
    does not email/toast the customer. The status still advances."""
    from order.enum.status import OrderStatus, PaymentStatus

    order = OrderFactory(
        status=OrderStatus.DELIVERED, payment_status=PaymentStatus.PENDING
    )
    AcsShipmentFactory(order=order, voucher_no="7227891234")

    _reconcile([_BASE_ROW], silent_for_customer=True)

    order.refresh_from_db()
    assert order.payment_status == PaymentStatus.COMPLETED
    assert order.status == OrderStatus.COMPLETED
    assert order.metadata.get("suppress_status_ws_COMPLETED") is True
