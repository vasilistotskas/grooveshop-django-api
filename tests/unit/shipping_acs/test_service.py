"""Unit tests for AcsService orchestration."""

from __future__ import annotations


import pytest

from order.factories.order import OrderFactory
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import AcsAPIError
from shipping_acs.factories import (
    AcsPickupListFactory,
    AcsShipmentFactory,
)
from shipping_acs.models import AcsShipment, AcsTrackingEvent
from shipping_acs.services import AcsService, _kg_from_grams

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Weight helper
# ---------------------------------------------------------------------------


class TestKgFromGrams:
    """Output uses Greek-locale comma-decimal — see services._kg_from_grams."""

    def test_zero_or_none_clamps_to_minimum(self):
        assert _kg_from_grams(None) == "0,5"
        assert _kg_from_grams(0) == "0,5"

    def test_below_minimum_clamps_up(self):
        assert _kg_from_grams(100) == "0,5"  # 0.1 kg → 0.5 floor

    def test_typical_grams_to_kg(self):
        assert _kg_from_grams(2500) == "2,5"

    def test_excessive_weight_clamps_down(self):
        assert _kg_from_grams(10**9) == "999,0"


# ---------------------------------------------------------------------------
# create_voucher_for_order
# ---------------------------------------------------------------------------


@pytest.fixture
def acs_client_mock(monkeypatch):
    """Patch shipping_acs.services.AcsClient so no real HTTP fires."""
    from shipping_acs import services

    class _DummyClient:
        billing_code = "TEST_BILLING"
        last_create_payload: dict | None = None
        last_multipart_call: str | None = None
        last_delete_call: str | None = None

        def create_voucher(self, params):
            _DummyClient.last_create_payload = params
            return {"Voucher_No": "7227891234", "Error_Message": ""}

        def get_multipart_vouchers(self, voucher_no):
            _DummyClient.last_multipart_call = voucher_no
            return ["8052453001", "8052453002"]

        def delete_voucher(self, voucher_no):
            _DummyClient.last_delete_call = voucher_no

        def issue_pickup_list(self, *, pickup_date):
            return {
                "PickupList_No": "9999000111",
                "Unprinted_Found": 0,
                "Error_Message": "",
            }

        def tracking_summary(self, voucher_no):
            return {
                "delivery_flag": 0,
                "returned_flag": 0,
                "shipment_status": 4,
            }

        def tracking_details(self, voucher_no):
            return [
                {
                    "checkpoint_date_time": "2025-09-13T14:39:15",
                    "checkpoint_action": "ΕΠΙΤΥΧΗΣ ΦΟΡΤΩΣΗ",
                    "checkpoint_location": "ΑΘΗΝΑ",
                    "checkpoint_notes": "",
                }
            ]

    monkeypatch.setattr(services, "AcsClient", _DummyClient)
    return _DummyClient


class TestCreateVoucherForOrder:
    def test_idempotent_when_voucher_already_set(self, acs_client_mock):
        order = OrderFactory()
        existing = AcsShipmentFactory(order=order, voucher_no="7777")

        result = AcsService.create_voucher_for_order(order)
        assert result.pk == existing.pk
        assert result.voucher_no == "7777"
        assert acs_client_mock.last_create_payload is None

    def test_persists_voucher_no_and_advances_state(self, acs_client_mock):
        order = OrderFactory()
        AcsShipmentFactory(order=order, item_quantity=1)

        result = AcsService.create_voucher_for_order(order)
        assert result.voucher_no == "7227891234"
        assert result.shipment_state == AcsShipmentState.NEW

    def test_persists_multipart_children_when_quantity_gt_one(
        self, acs_client_mock
    ):
        order = OrderFactory()
        AcsShipmentFactory(order=order, item_quantity=3)

        result = AcsService.create_voucher_for_order(order)
        assert result.metadata["multipart_vouchers"] == [
            "8052453001",
            "8052453002",
        ]
        assert acs_client_mock.last_multipart_call == "7227891234"

    def test_writes_tracking_info_on_order(self, acs_client_mock):
        order = OrderFactory()
        AcsShipmentFactory(order=order)

        AcsService.create_voucher_for_order(order)
        order.refresh_from_db()
        assert order.tracking_number == "7227891234"
        assert order.shipping_carrier == "acs"

    def test_raises_when_no_voucher_returned(self, monkeypatch):
        from shipping_acs import services

        class _BadClient:
            billing_code = "X"

            def create_voucher(self, params):
                return {"Voucher_No": "", "Error_Message": "Invalid address."}

        monkeypatch.setattr(services, "AcsClient", _BadClient)
        order = OrderFactory()
        AcsShipmentFactory(order=order)

        with pytest.raises(AcsAPIError) as exc_info:
            AcsService.create_voucher_for_order(order)
        assert "Invalid address" in str(exc_info.value)


# ---------------------------------------------------------------------------
# cancel_voucher
# ---------------------------------------------------------------------------


class TestCancelVoucher:
    def test_blocks_when_pickup_list_already_issued(self, acs_client_mock):
        order = OrderFactory()
        pickup = AcsPickupListFactory()
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="7227891234",
            shipment_state=AcsShipmentState.NEW,
            pickup_list=pickup,
        )

        with pytest.raises(AcsAPIError):
            AcsService.cancel_voucher(shipment, reason="customer changed mind")

    def test_calls_delete_when_eligible(self, acs_client_mock):
        order = OrderFactory()
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="7227891234",
            shipment_state=AcsShipmentState.NEW,
        )

        AcsService.cancel_voucher(shipment)
        shipment.refresh_from_db()
        assert shipment.shipment_state == AcsShipmentState.CANCELED
        assert acs_client_mock.last_delete_call == "7227891234"

    def test_idempotent_for_already_canceled(self, acs_client_mock):
        order = OrderFactory()
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="7227891234",
            shipment_state=AcsShipmentState.CANCELED,
        )
        AcsService.cancel_voucher(shipment)
        assert acs_client_mock.last_delete_call is None  # no-op


# ---------------------------------------------------------------------------
# poll_shipment_tracking
# ---------------------------------------------------------------------------


class TestPollShipmentTracking:
    def test_inserts_event_with_fingerprint(self, acs_client_mock):
        order = OrderFactory()
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="7227891234",
            shipment_state=AcsShipmentState.NEW,
        )

        result = AcsService.poll_shipment_tracking(shipment)
        assert result.shipment_state == AcsShipmentState.OUT_FOR_DELIVERY
        assert result.last_polled_at is not None
        assert AcsTrackingEvent.objects.filter(shipment=shipment).count() == 1

    def test_idempotent_on_repoll(self, acs_client_mock):
        order = OrderFactory()
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="7227891234",
            shipment_state=AcsShipmentState.NEW,
        )

        AcsService.poll_shipment_tracking(shipment)
        AcsService.poll_shipment_tracking(shipment)

        assert AcsTrackingEvent.objects.filter(shipment=shipment).count() == 1


@pytest.fixture
def acs_client_mock_delivered(monkeypatch):
    """Variant of ``acs_client_mock`` that drives summaries to DELIVERED.

    Used by the carrier-event → order-status integration tests
    (PR #6 S2). Mirrors the production payload shape: ``delivery_flag=1``
    + ``shipment_status=5`` (delivered) with a non-empty
    ``delivery_date`` so the post-delivery branches in
    ``poll_shipment_tracking`` exercise.
    """
    from shipping_acs import services

    class _DeliveredClient:
        billing_code = "TEST_BILLING"

        def tracking_summary(self, voucher_no):
            return {
                "delivery_flag": 1,
                "returned_flag": 0,
                "shipment_status": 5,
                "delivery_date": "2026-04-30T15:30:00",
            }

        def tracking_details(self, voucher_no):
            return [
                {
                    "checkpoint_date_time": "2026-04-30T15:30:00",
                    "checkpoint_action": "ΠΑΡΑΔΟΘΗΚΕ",
                    "checkpoint_location": "ΑΘΗΝΑ",
                    "checkpoint_notes": "",
                }
            ]

    monkeypatch.setattr(services, "AcsClient", _DeliveredClient)
    return _DeliveredClient


class TestPollShipmentDeliveryTransitions:
    """ACS DELIVERED tracking event → Order status auto-advance.

    PR #6 S2: BoxNow already had this coverage in
    tests/unit/shipping_boxnow/test_service.py
    (test_delivered_transitions_order_to_completed_when_paid +
    test_delivered_pauses_at_delivered_when_payment_pending). ACS
    didn't, so a regression in the auto-advance wiring would not
    have surfaced for the ACS path.
    """

    def test_paid_order_auto_completes_on_delivered(
        self, acs_client_mock_delivered
    ):
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.SHIPPED,
            payment_status=PaymentStatus.COMPLETED,
        )
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="9999998888",
            shipment_state=AcsShipmentState.OUT_FOR_DELIVERY,
        )

        AcsService.poll_shipment_tracking(shipment)

        order.refresh_from_db()
        shipment.refresh_from_db()
        assert shipment.shipment_state == AcsShipmentState.DELIVERED
        # PR #2 G — DELIVERED + payment_status=COMPLETED auto-completes.
        assert order.status == OrderStatus.COMPLETED

    def test_unpaid_cod_order_pauses_at_delivered(
        self, acs_client_mock_delivered
    ):
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.SHIPPED,
            payment_status=PaymentStatus.PENDING,
        )
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="9999998889",
            shipment_state=AcsShipmentState.OUT_FOR_DELIVERY,
        )

        AcsService.poll_shipment_tracking(shipment)

        order.refresh_from_db()
        shipment.refresh_from_db()
        assert shipment.shipment_state == AcsShipmentState.DELIVERED
        # COD — Order pauses at DELIVERED until reconcile_cod_payouts
        # flips payment_status to COMPLETED, then advances.
        assert order.status == OrderStatus.DELIVERED
        assert order.payment_status == PaymentStatus.PENDING

    def test_terminal_order_status_never_regresses(
        self, acs_client_mock_delivered
    ):
        """An admin-set terminal order status (DELIVERED / COMPLETED /
        CANCELED) is never overwritten by polling — the state-machine
        forward-only guard in OrderService.update_order_status filters
        backwards moves and ``_apply_order_status_transition`` early-
        returns when ``current_status in _TERMINAL_ORDER_STATUSES``."""
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.COMPLETED,
            payment_status=PaymentStatus.COMPLETED,
        )
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="9999998890",
            shipment_state=AcsShipmentState.DELIVERED,
        )

        AcsService.poll_shipment_tracking(shipment)

        order.refresh_from_db()
        # DELIVERED → COMPLETED is forward, so the previously-COMPLETED
        # order stays COMPLETED. Crucially, we never see backward moves
        # to DELIVERED or any earlier state.
        assert order.status == OrderStatus.COMPLETED


# ---------------------------------------------------------------------------
# issue_daily_pickup_list
# ---------------------------------------------------------------------------


class TestIssueDailyPickupList:
    def test_no_candidates_returns_none(self, acs_client_mock):
        result = AcsService.issue_daily_pickup_list()
        assert result is None

    def test_creates_pickup_list_and_links_shipments(self, acs_client_mock):
        order_a = OrderFactory()
        order_b = OrderFactory()
        AcsShipmentFactory(
            order=order_a,
            voucher_no="7227891111",
            shipment_state=AcsShipmentState.NEW,
        )
        AcsShipmentFactory(
            order=order_b,
            voucher_no="7227891222",
            shipment_state=AcsShipmentState.NEW,
        )

        result = AcsService.issue_daily_pickup_list()
        assert result is not None
        assert result.pickup_list_no == "9999000111"
        assert result.voucher_count == 2

        for shipment in AcsShipment.objects.filter(
            voucher_no__in=["7227891111", "7227891222"]
        ):
            assert shipment.pickup_list_id == result.id
