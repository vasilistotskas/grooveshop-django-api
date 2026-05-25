"""Phase 4c — AcsService.reconcile_cod_payouts upsert behaviour."""

from __future__ import annotations

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
    "POD": "POD-1",
    "Parcel_Sender": "GrooveShop",
    "Parcel_Receiver": "Customer",
    "Parcel_Pickup_Date": "2026-04-26T00:00:00",
    "Parcel_Delivery_Date": "2026-04-28T00:00:00",
    "Parcel_COD_Amount": 120.30,
    "Customer_RefNo_1": "1234",
    "Customer_RefNo_2": "",
    "COD_Amount_Cach": 0.00,
    "COD_Amount_CreditCard": 120.30,
    "Voucher_No": "7227891234",
    "COD_Payment_Date": "2026-04-29T00:00:00",
}


def test_reconcile_inserts_new_row_and_links_shipment():
    from order.enum.status import OrderStatus, PaymentStatus

    order = OrderFactory(
        status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
    )
    shipment = AcsShipmentFactory(order=order, voucher_no="7227891234")

    with patch("shipping_acs.services.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.cod_beneficiary_info.return_value = [_BASE_ROW]
        result = AcsService.reconcile_cod_payouts()

    assert result == {"upserted": 1, "linked": 1, "rows": 1}
    payout = AcsCodPayout.objects.get(voucher_no="7227891234")
    assert payout.shipment_id == shipment.id
    assert payout.cod_amount_total.amount == Decimal("120.30")
    assert payout.customer_ref_no_1 == "1234"


def test_reconcile_creates_orphan_row_when_shipment_missing():
    """ACS reports parcels we don't have AcsShipment rows for —
    those still need to be persisted, just without a FK link."""
    with patch("shipping_acs.services.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.cod_beneficiary_info.return_value = [
            {**_BASE_ROW, "Voucher_No": "9999000111"}
        ]
        result = AcsService.reconcile_cod_payouts()

    assert result == {"upserted": 1, "linked": 0, "rows": 1}
    payout = AcsCodPayout.objects.get(voucher_no="9999000111")
    assert payout.shipment_id is None


def test_reconcile_is_idempotent_on_voucher_date_pair():
    """Re-running the task with the same row updates rather than
    creates a duplicate — the unique constraint enforces it but
    update_or_create makes the path graceful."""
    AcsShipmentFactory(voucher_no="7227891234")

    with patch("shipping_acs.services.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.cod_beneficiary_info.return_value = [_BASE_ROW]
        AcsService.reconcile_cod_payouts()
        # Mutate the amount and re-run — same voucher+date should
        # update the existing row rather than crash on the unique key.
        instance.cod_beneficiary_info.return_value = [
            {**_BASE_ROW, "Parcel_COD_Amount": 200.00}
        ]
        AcsService.reconcile_cod_payouts()

    rows = AcsCodPayout.objects.filter(voucher_no="7227891234")
    assert rows.count() == 1
    assert rows.get().cod_amount_total.amount == Decimal("200.00")


def test_reconcile_skips_rows_without_voucher_no():
    """Garbage rows (missing voucher) are skipped — they wouldn't
    survive the unique constraint anyway."""
    with patch("shipping_acs.services.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.cod_beneficiary_info.return_value = [
            _BASE_ROW,
            {"Customer_Code": "x"},  # no Voucher_No
        ]
        result = AcsService.reconcile_cod_payouts()

    assert result["upserted"] == 1
    assert result["rows"] == 2


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
        with patch("shipping_acs.services.AcsClient") as mock_class:
            instance = mock_class.return_value
            instance.cod_beneficiary_info.return_value = [_BASE_ROW]
            AcsService.reconcile_cod_payouts()
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
        with patch("shipping_acs.services.AcsClient") as mock_class:
            instance = mock_class.return_value
            instance.cod_beneficiary_info.return_value = [_BASE_ROW]
            AcsService.reconcile_cod_payouts()
    finally:
        order_paid.disconnect(dispatch_uid="test.cod_no_refire")

    assert fired == []
