"""Vouchers ACS names that match no shipment are kept and raised (#622).

``ACS_Issue_Pickup_List`` refuses the day while any voucher of the account
is unprinted and names those vouchers (April 2026 manual, p. 14), the only
documented place a voucher we never recorded can surface before it is
final. Such a number may be the lost answer of a voucher request, a
duplicate, or a voucher made in ACS's own portal. The pickup-list run used
to drop it; now it is recorded, staff are told once, it is counted on the
dashboard until someone resolves it, and staff can delete it at ACS.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.utils import timezone

from order.enum.status import OrderStatus
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import AcsAPIError, AcsVoucherResolutionError
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsUnrecognisedVoucher
from shipping_acs.models.unrecognised_voucher import (
    AcsUnrecognisedVoucherResolution,
)
from shipping_acs.services import AcsService
from shipping_acs.tasks import issue_daily_acs_pickup_list
from tests.utils.orders import courier_cash_order

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

DELETE_NOTE = "Not ours: made in the ACS portal for a return."

OURS = "9700000101"
CHILD = "9700000102"
FOREIGN = "7401573174"


class _Acs:
    billing_code = "TEST_BILLING"
    named: list[str] = []
    deleted: list[str] = []
    delete_error: Exception | None = None

    def issue_pickup_list(self, *, pickup_date):
        return {
            "PickupList_No": None,
            "Unprinted_Found": len(_Acs.named),
            "Error_Message": "Αδύνατη η έκδοση λίστας παραλαβής.",
            "Unprinted_Vouchers": list(_Acs.named),
        }

    def get_pickup_lists(self, *, pickup_date):
        return []

    def delete_voucher(self, voucher_no):
        if _Acs.delete_error is not None:
            raise _Acs.delete_error
        _Acs.deleted.append(voucher_no)


@pytest.fixture
def acs(monkeypatch):
    from shipping_acs import services

    _Acs.named = [OURS, CHILD, FOREIGN]
    _Acs.deleted = []
    _Acs.delete_error = None
    monkeypatch.setattr(services, "AcsClient", _Acs)
    monkeypatch.setattr(
        "shipping_acs.tasks._skip_if_acs_unconfigured", lambda name: False
    )
    AcsShipmentFactory(
        voucher_no=OURS,
        shipment_state=AcsShipmentState.NEW,
        item_quantity=2,
        metadata={"multipart_vouchers": [CHILD]},
    )
    return _Acs


@pytest.fixture
def sent():
    with patch("shipping_acs.services.send_email") as send_email:
        yield send_email


class TestThePickupListRun:
    def test_a_voucher_matching_no_shipment_is_recorded_and_raised(
        self, acs, sent
    ):
        result = issue_daily_acs_pickup_list.run()

        assert result["status"] == "blocked_unprinted"
        assert result["unprinted"] == [OURS]
        assert result["unrecognised"] == [FOREIGN]
        row = AcsUnrecognisedVoucher.objects.get()
        assert row.voucher_no == FOREIGN
        assert row.pickup_date == timezone.localdate()
        assert row.resolved_at is None
        [call] = sent.call_args_list
        assert call.args[0] == "ops.acs_unrecognised_vouchers"
        assert call.args[1].vouchers == [FOREIGN]

    def test_a_voucher_named_again_is_not_raised_twice(self, acs, sent):
        issue_daily_acs_pickup_list.run()
        first_seen = AcsUnrecognisedVoucher.objects.get().last_seen_at

        issue_daily_acs_pickup_list.run()

        row = AcsUnrecognisedVoucher.objects.get()
        assert row.last_seen_at >= first_seen
        assert sent.call_count == 1

    def test_a_number_named_while_our_own_mint_is_in_flight_is_deferred(
        self, acs, sent
    ):
        """Review of #626, finding 3: ACS may name a voucher it minted for
        us a moment ago, before the mint saved it."""
        AcsShipmentFactory(
            voucher_no=None,
            shipment_state=AcsShipmentState.PENDING_CREATION,
            metadata={"mint_started_at": timezone.now().isoformat()},
        )

        result = issue_daily_acs_pickup_list.run()

        assert result["unrecognised"] == []
        assert not AcsUnrecognisedVoucher.objects.exists()
        sent.assert_not_called()

    def test_a_day_naming_only_our_vouchers_raises_nothing(self, acs, sent):
        acs.named = [OURS]

        issue_daily_acs_pickup_list.run()

        assert not AcsUnrecognisedVoucher.objects.exists()
        sent.assert_not_called()


class TestResolution:
    def _row(self):
        return AcsUnrecognisedVoucher.objects.create(
            voucher_no=FOREIGN,
            pickup_date=timezone.localdate(),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )

    def test_deleting_at_acs_resolves_it(self, acs):
        row = self._row()

        AcsService.delete_unrecognised_voucher(row, user=None, note=DELETE_NOTE)

        row.refresh_from_db()
        assert acs.deleted == [FOREIGN]
        assert row.resolution == AcsUnrecognisedVoucherResolution.DELETED
        assert row.resolved_at is not None
        # The dialog asks for a note: it is kept, as for "Mark as checked".
        assert row.resolution_note == DELETE_NOTE

    def test_a_duplicate_a_shipment_recorded_can_be_deleted(self, acs):
        """``orphan_vouchers``: a duplicate the mint could not delete. It
        is no shipment's voucher, and deleting it is the point."""
        AcsShipmentFactory(
            voucher_no="9700000777", metadata={"orphan_vouchers": [FOREIGN]}
        )
        row = self._row()

        AcsService.delete_unrecognised_voucher(row, user=None, note=DELETE_NOTE)

        assert acs.deleted == [FOREIGN]

    def test_an_acs_refusal_to_delete_leaves_it_open(self, acs):
        acs.delete_error = AcsAPIError(
            alias="ACS_Delete_Voucher",
            error_message="Δεν μπορεί να γίνει διαγραφή αποστολής ACS",
            raw={"ACSExecution_HasError": True},
        )
        row = self._row()

        with pytest.raises(AcsAPIError):
            AcsService.delete_unrecognised_voucher(
                row, user=None, note=DELETE_NOTE
            )

        row.refresh_from_db()
        assert row.resolved_at is None

    def test_deleting_is_refused_while_a_mint_is_in_flight(self, acs):
        row = self._row()
        AcsShipmentFactory(
            voucher_no=None,
            shipment_state=AcsShipmentState.PENDING_CREATION,
            metadata={"mint_started_at": timezone.now().isoformat()},
        )

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.delete_unrecognised_voucher(
                row, user=None, note=DELETE_NOTE
            )

        assert acs.deleted == []

    def test_deleting_is_refused_while_an_order_waits_for_its_lost_voucher(
        self, acs
    ):
        """Review of #626, finding 4: the voucher may be exactly that
        order's lost one; deleting it would destroy the only copy."""
        row = self._row()
        waiting = AcsShipmentFactory(
            # An order that still needs its voucher (the factory's own
            # order may be in any status, a canceled one included).
            order=courier_cash_order(status=OrderStatus.PENDING),
            voucher_no=None,
            shipment_state=AcsShipmentState.PENDING_CREATION,
            voucher_request_unknown_at=timezone.now(),
        )

        with pytest.raises(AcsVoucherResolutionError) as exc_info:
            AcsService.delete_unrecognised_voucher(
                row, user=None, note=DELETE_NOTE
            )

        assert f"#{waiting.order_id}" in str(exc_info.value)
        assert acs.deleted == []
        row.refresh_from_db()
        assert row.resolved_at is None

    def test_marking_it_checked_keeps_the_note(self, acs):
        row = self._row()

        AcsService.mark_unrecognised_voucher_checked(
            row, user=None, note="Made in the ACS portal for a return."
        )

        row.refresh_from_db()
        assert row.resolution == AcsUnrecognisedVoucherResolution.CHECKED
        assert row.resolution_note == "Made in the ACS portal for a return."

    def test_a_resolved_voucher_cannot_be_resolved_again(self, acs):
        row = self._row()
        AcsService.mark_unrecognised_voucher_checked(row, user=None, note="x")

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.delete_unrecognised_voucher(
                row, user=None, note=DELETE_NOTE
            )

        assert acs.deleted == []
