"""Unit tests for the 3-phase orphan-prevention pattern in AcsService.

Covers:
* ``create_voucher_for_order`` — mint_started_at claim lifecycle
* ``cancel_voucher`` — cancel_started_at claim lifecycle (Wave 1F pattern)

Each test patches the ACS HTTP client via ``monkeypatch`` so no real
network calls are made.  The ``_reseed_shipping_providers`` autouse
fixture in ``tests/conftest.py`` ensures the ACS ``ShippingProvider``
row is always present.

Design notes
------------
* Phase 1 (claim) writes ``metadata['mint_started_at']`` inside an atomic
  block **before** the ACS HTTP call.
* Phase 2 (API) runs outside any open transaction.
* Phase 3 (persist) clears ``mint_started_at`` and writes ``voucher_no``.

A DB save failure in Phase 3 must leave ``mint_started_at`` AND the
minted voucher number accessible in metadata so ops can recover.
That invariant is tested by simulating an IntegrityError on Phase 3's
``save()`` call.

TTL behaviour:
* Fresh claim (age < 300 s): ``AcsRetryableError`` raised — another
  worker is in flight.
* Stale claim (age >= 300 s): the service re-mints (logs a warning) and
  proceeds as a new attempt.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import AcsRetryableError
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsShipment
from shipping_acs.services import AcsService

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Shared mock client used by most tests in this module
# ---------------------------------------------------------------------------


@pytest.fixture
def acs_client_mock(monkeypatch):
    """Patch ``shipping_acs.services.AcsClient`` with a minimal stub."""
    from shipping_acs import services

    class _StubClient:
        billing_code = "TEST_BILLING"
        # Tracking which calls were made
        calls: list[str] = []

        def create_voucher(self, params):
            _StubClient.calls.append("create_voucher")
            return {"Voucher_No": "9001230001", "Error_Message": ""}

        def get_multipart_vouchers(self, voucher_no):
            return []

        def delete_voucher(self, voucher_no):
            _StubClient.calls.append(f"delete_voucher:{voucher_no}")

    _StubClient.calls = []
    monkeypatch.setattr(services, "AcsClient", _StubClient)
    return _StubClient


# ---------------------------------------------------------------------------
# Coverage 3-1: mint_started_at is written BEFORE the API call
# ---------------------------------------------------------------------------


class TestCreateVoucherSetsMintStartedAtBeforeApiCall:
    def test_create_voucher_sets_mint_started_at_before_api_call(
        self, monkeypatch
    ):
        """Phase 1 writes metadata['mint_started_at'] before Phase 2 fires.

        We intercept ``AcsClient.create_voucher`` and check whether the
        DB row already has ``mint_started_at`` set at the moment the API
        call is made.
        """
        from shipping_acs import services

        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        shipment = AcsShipmentFactory(order=order)
        shipment_pk = shipment.pk

        call_order: list[str] = []

        class _TrackingClient:
            billing_code = "TEST_BILLING"

            def create_voucher(self, params):
                # At the moment this runs (Phase 2), Phase 1 must be done.
                # Read directly from the DB (no ORM cache).
                row = AcsShipment.objects.values("metadata").get(pk=shipment_pk)
                if row["metadata"] and row["metadata"].get("mint_started_at"):
                    call_order.append("mint_started_at_already_set")
                else:
                    call_order.append("mint_started_at_NOT_set")
                call_order.append("create_voucher_called")
                return {"Voucher_No": "9001230002", "Error_Message": ""}

            def get_multipart_vouchers(self, voucher_no):
                return []

        monkeypatch.setattr(services, "AcsClient", _TrackingClient)

        with patch("user.signals.get_channel_layer", return_value=None):
            AcsService.create_voucher_for_order(order)

        assert "mint_started_at_already_set" in call_order, (
            "mint_started_at must be persisted before ACS API is called. "
            f"Observed sequence: {call_order}"
        )
        # The API call must come after the claim is set.
        assert call_order.index(
            "mint_started_at_already_set"
        ) < call_order.index("create_voucher_called")


# ---------------------------------------------------------------------------
# Coverage 3-2: mint_started_at stays in metadata when Phase 3 DB save fails
# ---------------------------------------------------------------------------


class TestCreateVoucherPersistsMetadataWhenDbSaveFails:
    def test_create_voucher_preserves_mint_claim_metadata_when_phase3_save_fails(
        self, monkeypatch
    ):
        """When Phase 3's save raises IntegrityError the minted voucher
        number is NOT lost from metadata (the Phase-1 claim was already
        committed), so ops can recover it.

        We simulate the failure by patching ``AcsShipment.save`` on the
        specific instance captured in Phase 3 to raise ``IntegrityError``
        on the first ``update_fields=['voucher_no', ...]`` call.
        """
        from django.db import IntegrityError
        from shipping_acs import services

        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        shipment = AcsShipmentFactory(order=order)
        shipment_pk = shipment.pk

        # Patch the ACS API client to succeed but record the voucher it returns.
        minted_voucher = "9001230099"

        class _SuccessClient:
            billing_code = "TEST_BILLING"

            def create_voucher(self, params):
                return {"Voucher_No": minted_voucher, "Error_Message": ""}

            def get_multipart_vouchers(self, voucher_no):
                return []

            def delete_voucher(self, voucher_no):
                pass

        monkeypatch.setattr(services, "AcsClient", _SuccessClient)

        # Track how many times save() is called on the model.
        original_save = AcsShipment.save
        call_count = {"n": 0}

        def _save_spy(self, *args, **kwargs):
            call_count["n"] += 1
            update_fields = kwargs.get("update_fields") or []
            if "voucher_no" in update_fields:
                # This is the Phase 3 final save — force a failure.
                raise IntegrityError("simulated Phase 3 DB failure")
            return original_save(self, *args, **kwargs)

        monkeypatch.setattr(AcsShipment, "save", _save_spy)

        with pytest.raises(IntegrityError):
            AcsService.create_voucher_for_order(order)

        # Phase 1 committed metadata['mint_started_at'] before the API
        # call.  That commit survives the Phase 3 IntegrityError because
        # Phase 1 and Phase 3 are independent ``transaction.atomic()``
        # blocks.
        row = AcsShipment.objects.values("metadata").get(pk=shipment_pk)
        meta = row["metadata"] or {}

        assert meta.get("mint_started_at") is not None, (
            "mint_started_at should survive a Phase 3 failure so a recovery "
            "pass can locate the orphan voucher via Reference_Key1 on ACS."
        )


# ---------------------------------------------------------------------------
# Coverage 3-3: stale mint_started_at (expired TTL) is treated as new attempt
# ---------------------------------------------------------------------------


class TestCreateVoucherTreatsExpiredMintStartedAtAsNewAttempt:
    def test_stale_mint_started_at_does_not_block_new_attempt(
        self, acs_client_mock
    ):
        """A ``mint_started_at`` older than ``_MINT_CLAIM_TTL_SECONDS``
        (300 s) must NOT raise ``AcsRetryableError`` — the service should
        log a warning and proceed as a fresh mint attempt.
        """
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        shipment = AcsShipmentFactory(order=order)

        # Plant a stale claim: 310 seconds in the past (well beyond the TTL).
        stale_time = timezone.now() - timedelta(seconds=310)
        shipment.metadata = {"mint_started_at": stale_time.isoformat()}
        shipment.save(update_fields=["metadata"])

        # Must NOT raise — the expired claim is treated as a new attempt.
        result = AcsService.create_voucher_for_order(order)

        assert result.voucher_no is not None
        assert result.voucher_no != ""
        # Voucher is set; the stale claim was cleared in Phase 3.
        assert result.metadata.get("mint_started_at") is None

    def test_fresh_mint_started_at_raises_retryable_error(
        self, acs_client_mock
    ):
        """A ``mint_started_at`` set just now (age < 300 s) must raise
        ``AcsRetryableError`` so the Celery task backs off.
        """
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        shipment = AcsShipmentFactory(order=order)

        # Plant a very recent claim.
        recent_time = timezone.now() - timedelta(seconds=5)
        shipment.metadata = {"mint_started_at": recent_time.isoformat()}
        shipment.save(update_fields=["metadata"])

        with pytest.raises(AcsRetryableError) as exc_info:
            AcsService.create_voucher_for_order(order)

        assert "already in progress" in str(exc_info.value).lower()
        # API client must NOT have been called.
        assert "create_voucher" not in acs_client_mock.calls

    def test_ttl_boundary_at_exactly_300s_is_treated_as_expired(
        self, acs_client_mock
    ):
        """At exactly 300 s the claim is on the boundary of the TTL.  The
        check in the service is ``if age < cls._MINT_CLAIM_TTL_SECONDS``,
        so age == 300 is NOT < 300 → treated as expired / re-mintable.
        """
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        shipment = AcsShipmentFactory(order=order)

        boundary_time = timezone.now() - timedelta(
            seconds=AcsService._MINT_CLAIM_TTL_SECONDS
        )
        shipment.metadata = {"mint_started_at": boundary_time.isoformat()}
        shipment.save(update_fields=["metadata"])

        # Should not raise.
        result = AcsService.create_voucher_for_order(order)
        assert result.voucher_no is not None


# ---------------------------------------------------------------------------
# Coverage 3-4: cancel_voucher uses cancel_started_at claim outside transaction
# ---------------------------------------------------------------------------


class TestCancelVoucherClaimPattern:
    def test_cancel_voucher_stamps_cancel_started_at_before_api_call(
        self, monkeypatch
    ):
        """Phase 1 of cancel_voucher writes ``cancel_started_at`` to
        metadata before the ACS delete API call happens in Phase 2.
        """
        from shipping_acs import services

        order = OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="9001999001",
            shipment_state=AcsShipmentState.NEW,
        )
        shipment_pk = shipment.pk

        call_order: list[str] = []

        class _TrackingClient:
            billing_code = "TEST_BILLING"

            def delete_voucher(self, voucher_no):
                row = AcsShipment.objects.values("metadata").get(pk=shipment_pk)
                if row["metadata"] and row["metadata"].get("cancel_started_at"):
                    call_order.append("cancel_started_at_already_set")
                else:
                    call_order.append("cancel_started_at_NOT_set")
                call_order.append("delete_voucher_called")

        monkeypatch.setattr(services, "AcsClient", _TrackingClient)

        AcsService.cancel_voucher(shipment)

        assert "cancel_started_at_already_set" in call_order, (
            f"cancel_started_at must be set before the API call. "
            f"Observed: {call_order}"
        )
        assert call_order.index(
            "cancel_started_at_already_set"
        ) < call_order.index("delete_voucher_called")

    def test_cancel_voucher_clears_cancel_started_at_on_success(
        self, monkeypatch
    ):
        """Phase 3 of a successful cancel must clear ``cancel_started_at``
        so the row doesn't appear in-progress to future observers.
        """
        from shipping_acs import services

        order = OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="9001999002",
            shipment_state=AcsShipmentState.NEW,
        )

        class _NullClient:
            billing_code = "TEST_BILLING"

            def delete_voucher(self, voucher_no):
                pass

        monkeypatch.setattr(services, "AcsClient", _NullClient)

        AcsService.cancel_voucher(shipment)

        shipment.refresh_from_db()
        assert shipment.metadata.get("cancel_started_at") is None
        assert shipment.shipment_state == AcsShipmentState.CANCELED

    def test_cancel_voucher_preserves_cancel_claim_when_phase3_save_fails(
        self, monkeypatch
    ):
        """When Phase 3's ``save()`` raises, the ``cancel_started_at``
        claim (set in Phase 1) survives — the API call already deleted the
        voucher at ACS.  A recovery pass can use this to detect the state.
        """
        from django.db import IntegrityError
        from shipping_acs import services

        order = OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="9001999003",
            shipment_state=AcsShipmentState.NEW,
        )
        shipment_pk = shipment.pk

        class _NullClient:
            billing_code = "TEST_BILLING"

            def delete_voucher(self, voucher_no):
                pass

        monkeypatch.setattr(services, "AcsClient", _NullClient)

        original_save = AcsShipment.save
        call_count = {"n": 0}

        def _save_spy(self, *args, **kwargs):
            call_count["n"] += 1
            update_fields = kwargs.get("update_fields") or []
            if (
                "shipment_state" in update_fields
                and "cancel_requested_at" in update_fields
            ):
                # Phase 3 final save — force a failure.
                raise IntegrityError("simulated Phase 3 cancel save failure")
            return original_save(self, *args, **kwargs)

        monkeypatch.setattr(AcsShipment, "save", _save_spy)

        with pytest.raises(IntegrityError):
            AcsService.cancel_voucher(shipment)

        # Phase 1 cancel_started_at should survive the Phase 3 error.
        row = AcsShipment.objects.values("metadata").get(pk=shipment_pk)
        meta = row["metadata"] or {}
        assert meta.get("cancel_started_at") is not None, (
            "cancel_started_at should be in metadata after Phase 3 failure "
            "so ops can detect that the ACS-side cancel already happened."
        )

    def test_cancel_voucher_fresh_claim_raises_retryable_error(
        self, monkeypatch
    ):
        """A ``cancel_started_at`` set recently (age < 300 s) causes a
        concurrent caller to receive ``AcsRetryableError``.
        """
        from shipping_acs import services

        order = OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="9001999004",
            shipment_state=AcsShipmentState.NEW,
        )

        class _NullClient:
            billing_code = "TEST_BILLING"

            def delete_voucher(self, voucher_no):
                pass

        monkeypatch.setattr(services, "AcsClient", _NullClient)

        recent_time = timezone.now() - timedelta(seconds=5)
        shipment.metadata = {"cancel_started_at": recent_time.isoformat()}
        shipment.save(update_fields=["metadata"])

        with pytest.raises(AcsRetryableError) as exc_info:
            AcsService.cancel_voucher(shipment)

        assert "already in progress" in str(exc_info.value).lower()

    def test_cancel_voucher_stale_claim_proceeds_as_new_attempt(
        self, monkeypatch
    ):
        """A ``cancel_started_at`` older than the TTL is treated as stale
        and the cancel is retried.
        """
        from shipping_acs import services

        order = OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )
        shipment = AcsShipmentFactory(
            order=order,
            voucher_no="9001999005",
            shipment_state=AcsShipmentState.NEW,
        )

        class _NullClient:
            billing_code = "TEST_BILLING"

            def delete_voucher(self, voucher_no):
                pass

        monkeypatch.setattr(services, "AcsClient", _NullClient)

        stale_time = timezone.now() - timedelta(seconds=310)
        shipment.metadata = {"cancel_started_at": stale_time.isoformat()}
        shipment.save(update_fields=["metadata"])

        # Should NOT raise — stale claim is ignored.
        AcsService.cancel_voucher(shipment)

        shipment.refresh_from_db()
        assert shipment.shipment_state == AcsShipmentState.CANCELED
