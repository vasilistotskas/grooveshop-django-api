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
from shipping_acs.services import (
    AcsService,
    _kg_from_grams,
    _normalize_phone_for_acs,
    _normalize_zipcode_for_acs,
)

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
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
        existing = AcsShipmentFactory(order=order, voucher_no="7777")

        result = AcsService.create_voucher_for_order(order)
        assert result.pk == existing.pk
        assert result.voucher_no == "7777"
        assert acs_client_mock.last_create_payload is None

    def test_persists_voucher_no_and_advances_state(self, acs_client_mock):
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
        AcsShipmentFactory(order=order, item_quantity=1)

        result = AcsService.create_voucher_for_order(order)
        assert result.voucher_no == "7227891234"
        assert result.shipment_state == AcsShipmentState.NEW

    def test_persists_multipart_children_when_quantity_gt_one(
        self, acs_client_mock
    ):
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
        AcsShipmentFactory(order=order, item_quantity=3)

        result = AcsService.create_voucher_for_order(order)
        assert result.metadata["multipart_vouchers"] == [
            "8052453001",
            "8052453002",
        ]
        assert acs_client_mock.last_multipart_call == "7227891234"

    def test_writes_tracking_info_on_order(self, acs_client_mock):
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
        AcsShipmentFactory(order=order)

        AcsService.create_voucher_for_order(order)
        order.refresh_from_db()
        assert order.tracking_number == "7227891234"
        assert order.shipping_carrier == "acs"

    def test_raises_when_no_voucher_returned(self, monkeypatch):
        from order.enum.status import OrderStatus, PaymentStatus
        from shipping_acs import services

        class _BadClient:
            billing_code = "X"

            def create_voucher(self, params):
                return {"Voucher_No": "", "Error_Message": "Invalid address."}

        monkeypatch.setattr(services, "AcsClient", _BadClient)
        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
        AcsShipmentFactory(order=order)

        with pytest.raises(AcsAPIError) as exc_info:
            AcsService.create_voucher_for_order(order)
        assert "Invalid address" in str(exc_info.value)


# ---------------------------------------------------------------------------
# cancel_voucher
# ---------------------------------------------------------------------------


class TestCancelVoucher:
    def test_blocks_when_pickup_list_already_issued(self, acs_client_mock):
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
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
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
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
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
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
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
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
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
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

    def test_paid_delivered_suppresses_completed_email_and_toast(
        self, acs_client_mock_delivered
    ):
        """PR #7: when DELIVERED auto-advances to COMPLETED via the
        carrier path, the customer must not get back-to-back DELIVERED
        + COMPLETED emails or toasts. The DELIVERED transition fires
        normally; COMPLETED is suppressed."""
        from order.enum.status import OrderStatus, PaymentStatus
        from order.tasks import _status_update_reservation_key

        order = OrderFactory(
            status=OrderStatus.SHIPPED,
            payment_status=PaymentStatus.COMPLETED,
        )
        AcsShipmentFactory(
            order=order,
            voucher_no="9999998891",
            shipment_state=AcsShipmentState.OUT_FOR_DELIVERY,
        )

        AcsService.poll_shipment_tracking(order.acs_shipment)

        order.refresh_from_db()
        # Sanity: state machine landed at COMPLETED.
        assert order.status == OrderStatus.COMPLETED
        meta = order.metadata or {}
        # Email reservation flag is pre-stamped → email task short-
        # circuits the COMPLETED send.
        completed_email_flag = _status_update_reservation_key(
            order.id, OrderStatus.COMPLETED.value
        )
        assert meta.get(completed_email_flag) is True
        # WS suppression flag is set → handle_order_status_changed
        # skips the COMPLETED toast dispatch.
        assert (
            meta.get(f"suppress_status_ws_{OrderStatus.COMPLETED.value}")
            is True
        )
        # DELIVERED's customer notifications were NOT suppressed; only
        # COMPLETED was. The DELIVERED email reservation flag is
        # whatever the email task itself set when it ran (truthy if
        # eager-fired, missing if the task was mocked) — we don't
        # assert on it here. Just confirm the WS suppression flag for
        # DELIVERED is absent.
        assert (
            meta.get(f"suppress_status_ws_{OrderStatus.DELIVERED.value}")
            is None
        )

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
        from order.enum.status import OrderStatus, PaymentStatus

        order_a = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
        order_b = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
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


# ---------------------------------------------------------------------------
# Field normalizers — locked after order 57 (2026-05-15) was rejected by ACS
# with "Error fill data error" because the customer's zipcode "848 00"
# (Greek convention) and PhoneNumberField's E.164 "+306989424342" were
# passed verbatim. ACS sample payload uses digits-only zipcode + bare
# 10-digit local phone.
# ---------------------------------------------------------------------------


class TestNormalizeZipcodeForAcs:
    def test_strips_internal_whitespace(self):
        assert _normalize_zipcode_for_acs("848 00") == "84800"

    def test_strips_punctuation_and_keeps_first_five(self):
        assert _normalize_zipcode_for_acs("12345-6") == "12345"

    def test_blank_returns_empty(self):
        assert _normalize_zipcode_for_acs("") == ""
        assert _normalize_zipcode_for_acs(None) == ""

    def test_truncates_to_five_digits(self):
        assert _normalize_zipcode_for_acs("123456789") == "12345"

    def test_handles_integer_input(self):
        assert _normalize_zipcode_for_acs(17778) == "17778"


class TestNormalizePhoneForAcs:
    def test_strips_e164_country_code(self):
        assert _normalize_phone_for_acs("+306989424342") == "6989424342"

    def test_strips_double_zero_country_code(self):
        assert _normalize_phone_for_acs("00306989424342") == "6989424342"

    def test_local_format_unchanged(self):
        assert _normalize_phone_for_acs("6989424342") == "6989424342"

    def test_blank_returns_empty(self):
        assert _normalize_phone_for_acs("") == ""
        assert _normalize_phone_for_acs(None) == ""

    def test_phonenumber_object_uses_national_number(self):
        # PhoneNumberField returns a phonenumbers.PhoneNumber whose
        # ``national_number`` is the canonical local-format integer.
        # We can't easily import phonenumbers here without adding a
        # dependency to the test setup, so simulate with a stub object.
        class _PhoneStub:
            national_number = 6989424342

        assert _normalize_phone_for_acs(_PhoneStub()) == "6989424342"

    def test_ambiguous_length_left_untouched(self):
        # 30 prefix + 9 digits doesn't match the 10-digit local rule —
        # leave it so ACS rejects loudly instead of silently truncating.
        assert _normalize_phone_for_acs("30123456789") == "30123456789"


# ---------------------------------------------------------------------------
# last_error persistence
# ---------------------------------------------------------------------------


class TestLastErrorPersisted:
    """``_record_last_error`` must capture the failing payload so admins
    can diagnose ACS rejections without re-running the request builder."""

    def test_writes_request_and_error_to_metadata_when_voucher_no_empty(
        self, monkeypatch
    ):
        from order.enum.status import OrderStatus, PaymentStatus
        from shipping_acs import services

        class _BadClient:
            billing_code = "X"

            def create_voucher(self, params):
                return {
                    "Voucher_No": "",
                    "Error_Message": "ACS:Error fill data error",
                }

        monkeypatch.setattr(services, "AcsClient", _BadClient)
        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
        shipment = AcsShipmentFactory(order=order)

        with pytest.raises(AcsAPIError):
            AcsService.create_voucher_for_order(order)

        shipment.refresh_from_db()
        last_error = shipment.metadata.get("last_error")
        assert last_error is not None
        assert "Error fill data error" in last_error["error"]
        assert "Recipient_Zipcode" in last_error["request_params"]
        assert last_error["occurred_at"]
        # mint_started_at must be released so the next retry isn't
        # stuck waiting for the 300s TTL.
        assert "mint_started_at" not in shipment.metadata

    def test_writes_request_and_error_to_metadata_when_client_raises(
        self, monkeypatch
    ):
        from order.enum.status import OrderStatus, PaymentStatus
        from shipping_acs import services

        class _ExplodingClient:
            billing_code = "X"

            def create_voucher(self, params):
                raise ConnectionError("ACS host unreachable")

        monkeypatch.setattr(services, "AcsClient", _ExplodingClient)
        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
        shipment = AcsShipmentFactory(order=order)

        with pytest.raises(ConnectionError):
            AcsService.create_voucher_for_order(order)

        shipment.refresh_from_db()
        last_error = shipment.metadata.get("last_error")
        assert last_error is not None
        assert "ACS host unreachable" in last_error["error"]
        assert last_error["request_params"]["Billing_Code"] == "X"
        assert "mint_started_at" not in shipment.metadata


# ---------------------------------------------------------------------------
# Payload builder — zipcode/phone flow from order → ACS
# ---------------------------------------------------------------------------


class TestBuildCreateVoucherParamsNormalizes:
    """End-to-end check that the normalizers are wired into the
    outbound ACS payload — guards against a future edit dropping
    them silently."""

    def test_normalizes_zipcode_and_phone_in_outgoing_payload(
        self, acs_client_mock
    ):
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            zipcode="848 00",
            phone="+306989424342",
        )
        AcsShipmentFactory(order=order)

        AcsService.create_voucher_for_order(order)

        sent = acs_client_mock.last_create_payload
        assert sent["Recipient_Zipcode"] == "84800"
        assert sent["Recipient_Phone"] == "6989424342"
        assert sent["Recipient_Cell_Phone"] == "6989424342"


# ---------------------------------------------------------------------------
# Delivery_Notes — site owner reported on 2026-05-16 that the checkout
# "παρατηρήσεις/σημειώσεις" field was not appearing on the courier
# voucher. The helper lives in shipping.services so BoxNow can reuse
# it; these tests lock both the normalisation and the wiring into
# the ACS payload.
# ---------------------------------------------------------------------------


class TestSanitizeDeliveryNotes:
    def test_collapses_whitespace_runs(self):
        from shipping.services import sanitize_delivery_notes

        assert (
            sanitize_delivery_notes("Ring   twice.\n\n  Leave\twith porter.")
            == "Ring twice. Leave with porter."
        )

    def test_blank_returns_empty(self):
        from shipping.services import sanitize_delivery_notes

        assert sanitize_delivery_notes("") == ""
        assert sanitize_delivery_notes(None) == ""

    def test_truncates_to_500_chars(self):
        from shipping.services import (
            DELIVERY_NOTES_MAX_LEN,
            sanitize_delivery_notes,
        )

        long_note = "α" * 800
        assert len(sanitize_delivery_notes(long_note)) == DELIVERY_NOTES_MAX_LEN


class TestDeliveryNotesInPayload:
    def test_customer_notes_lands_in_Delivery_Notes(self, acs_client_mock):
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            customer_notes="Χτυπήστε το κουδούνι 2  φορές.\nΘυρωρός.",
        )
        AcsShipmentFactory(order=order)

        AcsService.create_voucher_for_order(order)

        sent = acs_client_mock.last_create_payload
        assert (
            sent["Delivery_Notes"] == "Χτυπήστε το κουδούνι 2 φορές. Θυρωρός."
        )

    def test_empty_customer_notes_sends_empty_string(self, acs_client_mock):
        from order.enum.status import OrderStatus, PaymentStatus

        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            customer_notes="",
        )
        AcsShipmentFactory(order=order)

        AcsService.create_voucher_for_order(order)
        assert acs_client_mock.last_create_payload["Delivery_Notes"] == ""
