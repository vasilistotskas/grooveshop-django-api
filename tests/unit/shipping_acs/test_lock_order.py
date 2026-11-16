"""Every ACS path that locks a shipment with its order takes the order
first (#613).

The rule is one function, ``shipping.locks.lock_with_order``. A path that
went back to ``select_for_update().select_related("order")`` would lock the
shipment row before its order's, the opposite of every other path, and two
of them could then wait on each other until PostgreSQL aborted one.
"""

from __future__ import annotations

import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from shipping_acs import services
from shipping_acs.enum.charge_type import AcsChargeType
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.services import AcsService
from tests.utils.orders import courier_cash_order

pytestmark = pytest.mark.django_db

ORDER_THEN_SHIPMENT = ["order_order", "shipping_acs_acsshipment"]
VOUCHER = "7227891234"


class _Acs:
    """The ACS HTTP boundary."""

    billing_code = "TEST_BILLING"

    def create_voucher(self, params, *, request_id):
        return {"Voucher_No": VOUCHER, "Error_Message": ""}

    def get_multipart_vouchers(self, voucher_no):
        return []

    def delete_voucher(self, voucher_no):
        return None

    def tracking_summary(self, voucher_no):
        return {"shipment_status": 2, "delivery_flag": 0, "returned_flag": 0}

    def tracking_details(self, voucher_no):
        return []


@pytest.fixture(autouse=True)
def _acs(monkeypatch):
    monkeypatch.setattr(services, "AcsClient", _Acs)


def _locks(run) -> list[str]:
    """The table each ``SELECT ... FOR UPDATE`` of *run* locks, in order.

    A locking statement that joins another table locks the rows of both in
    one step, the shipment's first: that is the shape this file forbids.
    """
    with CaptureQueriesContext(connection) as captured:
        run()
    tables = []
    for query in captured.captured_queries:
        sql = query["sql"]
        if "FOR UPDATE" not in sql:
            continue
        assert " JOIN " not in sql, sql
        match = re.search(r'FROM "(\w+)"', sql)
        tables.append(match.group(1) if match else sql)
    return tables


def _order(**kwargs):
    kwargs.setdefault("status", OrderStatus.PROCESSING)
    kwargs.setdefault("payment_status", PaymentStatus.PENDING)
    return courier_cash_order(**kwargs)


def _unknown(**kwargs):
    """A shipment whose voucher request has an unknown outcome."""
    return AcsShipmentFactory(
        order=_order(**kwargs),
        voucher_request_unknown_at=timezone.now() - timedelta(hours=1),
    )


def test_the_voucher_creation_claims_and_saves_under_the_order_lock():
    order = _order()
    AcsShipmentFactory(order=order, item_quantity=1)

    locks = _locks(lambda: AcsService.create_voucher_for_order(order))

    claim = locks.index("shipping_acs_acsshipment")
    save = locks.index("shipping_acs_acsshipment", claim + 1)
    assert locks[claim - 1 : claim + 1] == ORDER_THEN_SHIPMENT
    assert locks[save - 1 : save + 1] == ORDER_THEN_SHIPMENT


def test_the_tracking_poll():
    shipment = AcsShipmentFactory(
        order=_order(), voucher_no=VOUCHER, shipment_state=AcsShipmentState.NEW
    )

    locks = _locks(lambda: AcsService.poll_shipment_tracking(shipment))

    assert locks[:2] == ORDER_THEN_SHIPMENT


def test_a_poll_that_finds_the_shipment_canceled_cancels_the_order():
    """The sync cancels the order of a shipment canceled before it left
    (``cancel_for_dead_shipment``) inside its own transaction: the order
    row it writes is the first one it locked."""
    shipment = AcsShipmentFactory(
        order=_order(), voucher_no=VOUCHER, shipment_state=AcsShipmentState.NEW
    )

    def poll_a_canceled_shipment() -> None:
        with patch.object(
            AcsShipmentState,
            "from_tracking_summary",
            return_value=AcsShipmentState.CANCELED,
        ):
            AcsService.poll_shipment_tracking(shipment)

    locks = _locks(poll_a_canceled_shipment)

    assert locks[:2] == ORDER_THEN_SHIPMENT
    shipment.order.refresh_from_db()
    assert shipment.order.status == OrderStatus.CANCELED


def test_adopting_a_voucher():
    shipment = _unknown()

    with patch.object(
        AcsService, "_confirm_voucher_at_acs", return_value="acs"
    ):
        locks = _locks(
            lambda: AcsService.adopt_voucher(shipment, VOUCHER, user=None)
        )

    assert locks[:2] == ORDER_THEN_SHIPMENT


def test_requesting_a_voucher_again():
    shipment = _unknown()

    with patch.object(services, "dispatch_on_commit"):
        locks = _locks(
            lambda: AcsService.request_voucher_again(
                shipment, user=None, note="nothing at ACS"
            )
        )

    assert locks == ORDER_THEN_SHIPMENT


def test_closing_a_voucher_request():
    shipment = _unknown(status=OrderStatus.CANCELED)

    locks = _locks(
        lambda: AcsService.close_voucher_request(
            shipment, user=None, note="nothing at ACS"
        )
    )

    assert locks == ORDER_THEN_SHIPMENT


def test_recording_cash_received_outside_acs(django_user_model):
    shipment = AcsShipmentFactory(
        order=_order(status=OrderStatus.DELIVERED),
        voucher_no=VOUCHER,
        shipment_state=AcsShipmentState.DELIVERED,
        charge_type=AcsChargeType.COD,
        cod_amount=Money("24.48", "EUR"),
    )
    staff = django_user_model(pk=1)

    locks = _locks(
        lambda: AcsService.record_cod_received_outside_acs(
            shipment, user=staff, note="paid by bank transfer"
        )
    )

    assert locks[:2] == ORDER_THEN_SHIPMENT


def test_a_voucher_cancel_locks_the_shipment_alone():
    """It never touches the order, so it takes no order lock: an order
    cancel asks the courier with its own transaction over."""
    shipment = AcsShipmentFactory(
        order=_order(), voucher_no=VOUCHER, shipment_state=AcsShipmentState.NEW
    )

    locks = _locks(lambda: AcsService.cancel_voucher(shipment, reason="x"))

    assert set(locks) == {"shipping_acs_acsshipment"}
    shipment.refresh_from_db()
    assert shipment.shipment_state == AcsShipmentState.CANCELED
