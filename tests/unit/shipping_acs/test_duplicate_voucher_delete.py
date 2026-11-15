"""A mint's unused voucher is deleted at ACS with no row lock held (#640).

When another worker saved a voucher while this mint's request was out,
this mint's answer is a duplicate and is deleted at ACS. Phase 3 decides
that under the shipment's row lock, records the number on the row
(``metadata["orphan_vouchers"]``) and commits; only then is ACS asked to
delete it. So no staff action waits on the row for the length of an ACS
call, and no outcome of the delete can lose the number.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.utils import timezone

from order.enum.status import OrderStatus
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import AcsAPIError, AcsOutcomeUnknownError
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsShipment, AcsUnrecognisedVoucher
from shipping_acs.models.unrecognised_voucher import (
    AcsUnrecognisedVoucherResolution,
    AcsUnrecognisedVoucherSource,
)
from shipping_acs.services import AcsService
from tests.utils.orders import courier_cash_order

pytestmark = pytest.mark.django_db

OURS = "7401573180"
WINNER = "7401573181"


class _Acs:
    """ACS answering this mint with ``OURS`` after another worker saved
    ``WINNER`` on the same shipment."""

    billing_code = "TEST_BILLING"
    shipment_pk: int = 0
    open_atomic_blocks_during_create: int = -1
    seen_by_delete: dict = {}
    delete_error: Exception | None = None
    deleted: list[str] = []

    def create_voucher(self, params, *, request_id):
        _Acs.open_atomic_blocks_during_create = len(connection.atomic_blocks)
        AcsShipment.objects.filter(pk=_Acs.shipment_pk).update(
            voucher_no=WINNER, shipment_state=AcsShipmentState.NEW
        )
        return {"Voucher_No": OURS, "Error_Message": ""}

    def get_multipart_vouchers(self, voucher_no):
        return []

    def delete_voucher(self, voucher_no):
        row = AcsShipment.objects.values("metadata").get(pk=_Acs.shipment_pk)
        _Acs.seen_by_delete = {
            "open_atomic_blocks": len(connection.atomic_blocks),
            "orphan_vouchers": (row["metadata"] or {}).get("orphan_vouchers"),
        }
        if _Acs.delete_error is not None:
            raise _Acs.delete_error
        _Acs.deleted.append(voucher_no)


@pytest.fixture
def shipment(monkeypatch):
    from shipping_acs import services

    shipment = AcsShipmentFactory(
        order=courier_cash_order(status=OrderStatus.PENDING),
        voucher_no=None,
        shipment_state=AcsShipmentState.PENDING_CREATION,
    )
    _Acs.shipment_pk = shipment.pk
    _Acs.open_atomic_blocks_during_create = -1
    _Acs.seen_by_delete = {}
    _Acs.delete_error = None
    _Acs.deleted = []
    monkeypatch.setattr(services, "AcsClient", _Acs)
    return shipment


def _mint(shipment) -> AcsShipment:
    AcsService.create_voucher_for_order(shipment.order)
    return AcsShipment.objects.get(pk=shipment.pk)


class TestTheDuplicateDeleteHoldsNoLock:
    def test_acs_is_asked_outside_the_shipments_transaction(self, shipment):
        _mint(shipment)

        # As many atomic blocks open as during the create, which runs with
        # no lock: the delete is not inside Phase 3's ``select_for_update``.
        assert (
            _Acs.seen_by_delete["open_atomic_blocks"]
            == _Acs.open_atomic_blocks_during_create
        )

    def test_the_number_is_on_the_row_before_acs_is_asked(self, shipment):
        _mint(shipment)

        assert _Acs.seen_by_delete["orphan_vouchers"] == [OURS]


class TestTheOutcomeOfTheDelete:
    def test_a_deleted_duplicate_leaves_no_orphan(self, shipment):
        AcsUnrecognisedVoucher.objects.create(
            voucher_no=OURS,
            pickup_date=timezone.localdate(),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
            source=AcsUnrecognisedVoucherSource.PICKUP_LIST,
        )

        saved = _mint(shipment)

        assert _Acs.deleted == [OURS]
        assert saved.voucher_no == WINNER
        assert "orphan_vouchers" not in saved.metadata
        row = AcsUnrecognisedVoucher.objects.get(voucher_no=OURS)
        assert row.resolution == AcsUnrecognisedVoucherResolution.DELETED

    def test_a_refused_delete_keeps_the_orphan(self, shipment):
        _Acs.delete_error = AcsAPIError(
            alias="ACS_Delete_Voucher", error_message="refused"
        )

        saved = _mint(shipment)

        assert saved.voucher_no == WINNER
        assert saved.metadata["orphan_vouchers"] == [OURS]
        row = AcsUnrecognisedVoucher.objects.get(voucher_no=OURS)
        assert row.source == AcsUnrecognisedVoucherSource.REFUSED_DELETE
        assert row.duplicate_of_id == shipment.pk

    def test_a_lost_answer_to_the_delete_keeps_the_orphan(self, shipment):
        _Acs.delete_error = AcsOutcomeUnknownError(
            alias="ACS_Delete_Voucher", error_message="Read timed out."
        )

        saved = _mint(shipment)

        assert saved.metadata["orphan_vouchers"] == [OURS]
        assert AcsUnrecognisedVoucher.objects.filter(voucher_no=OURS).exists()

    def test_an_unexpected_error_from_the_delete_keeps_the_number(
        self, shipment
    ):
        """Before #640 an exception that is not an ``AcsAPIError`` rolled
        Phase 3 back, and the voucher number was lost."""
        _Acs.delete_error = RuntimeError("socket closed")

        saved = _mint(shipment)

        assert saved.voucher_no == WINNER
        assert saved.metadata["orphan_vouchers"] == [OURS]
        row = AcsUnrecognisedVoucher.objects.get(voucher_no=OURS)
        assert row.source == AcsUnrecognisedVoucherSource.REFUSED_DELETE
