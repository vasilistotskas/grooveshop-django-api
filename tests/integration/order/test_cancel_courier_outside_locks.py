"""An order cancel asks the courier with no row locked (#713), and a
carrier sync and a staff cancel never wait on each other (#613).

``OrderService._cancel_order_locked`` locked the order (and the products it
restocks) and then called the courier inside that transaction: every row
stayed locked across the carrier's HTTP round trip, and the shipment was
locked after the order while a tracking sync took the two the other way
round. The cancel now commits first. The courier is asked afterwards, with
no transaction open, and a cancel whose worker stopped in between is
finished by ``finish_pending_shipment_cancels``.

These tests commit for real and look at the database from a second
connection, at the moment the carrier's HTTP boundary is called.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.db import OperationalError, connection, transaction
from django.utils import timezone

from order.enum.status import OrderStatus, PaymentStatus
from order.models.history import OrderHistory
from order.models.order import Order
from order.services import SHIPMENT_CANCEL_PENDING_FLAG, OrderService
from order.stock import StockManager
from order.tasks import (
    auto_cancel_stuck_pending_orders,
    finish_pending_shipment_cancels,
)
from pay_way.factories import PayWayFactory
from shipping.exceptions import CarrierUnavailableError
from shipping.models import ShippingProvider
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsShipment
from shipping_acs.services import AcsService
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.factories import BoxNowShipmentFactory
from tests.utils.orders import courier_cash_order

pytestmark = pytest.mark.django_db(transaction=True)

WAIT = 10
ASKED = {"attempted": True, "dispatched": True}
NOTHING_PENDING = {"finished": 0, "still_pending": 0}
ONE_FINISHED = {"finished": 1, "still_pending": 0}
ONE_STILL_PENDING = {"finished": 0, "still_pending": 1}


def _on_another_connection(read) -> Any:
    """What *read* returns on a connection of its own."""
    answer: list[Any] = []

    def run() -> None:
        try:
            answer.append(read())
        except BaseException as exc:
            answer.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(WAIT)
    assert answer, "the second connection gave no answer"
    if isinstance(answer[0], BaseException):
        raise answer[0]
    return answer[0]


def _lockable(model, pk: int) -> bool:
    """Whether another connection can lock the row at once."""

    def read() -> bool:
        try:
            with transaction.atomic():
                model.objects.select_for_update(nowait=True).get(pk=pk)
        except OperationalError:
            return False
        return True

    return _on_another_connection(read)


def _committed_status(order_id: int) -> str:
    return _on_another_connection(
        lambda: Order.objects.values_list("status", flat=True).get(pk=order_id)
    )


@dataclass(frozen=True)
class Seen:
    """The database as it was while the courier was being asked."""

    in_transaction: bool
    order_lockable: bool
    shipment_lockable: bool
    order_status: str


UNLOCKED = Seen(
    in_transaction=False,
    order_lockable=True,
    shipment_lockable=True,
    order_status=OrderStatus.CANCELED,
)


class Courier:
    """The HTTP boundary of both carriers (``AcsClient`` /
    ``BoxNowClient``): each cancel records what it saw."""

    def __init__(self, shipment, *, error: Exception | None = None) -> None:
        self.shipment = shipment
        self.error = error
        self.calls: list[Seen] = []
        self.during_call = lambda: None

    def __call__(self) -> Courier:
        return self

    def _cancel(self, _identifier: str) -> None:
        self.calls.append(
            Seen(
                in_transaction=connection.in_atomic_block,
                order_lockable=_lockable(Order, self.shipment.order_id),
                shipment_lockable=_lockable(
                    type(self.shipment), self.shipment.pk
                ),
                order_status=_committed_status(self.shipment.order_id),
            )
        )
        self.during_call()
        if self.error is not None:
            raise self.error

    delete_voucher = _cancel
    cancel_parcel = _cancel

    def tracking_summary(self, _voucher_no: str) -> dict[str, Any]:
        return {"shipment_status": "2", "delivery_flag": "0"}

    def tracking_details(self, _voucher_no: str) -> list[dict[str, Any]]:
        return []


def _order(provider_code: str) -> Order:
    return courier_cash_order(
        shipping_provider=ShippingProvider.objects.get(code=provider_code),
        shipping_kind=(
            "pickup_point" if provider_code == "boxnow" else "home_delivery"
        ),
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.PENDING,
    )


def _shipment(provider_code: str):
    order = _order(provider_code)
    if provider_code == "acs":
        return AcsShipmentFactory(order=order, with_voucher=True)
    return BoxNowShipmentFactory(order=order, with_parcel=True)


def _courier(monkeypatch, shipment, **kwargs) -> Courier:
    courier = Courier(shipment, **kwargs)
    monkeypatch.setattr("shipping_acs.services.AcsClient", courier)
    monkeypatch.setattr("shipping_boxnow.services.BoxNowClient", courier)
    return courier


def _staff_cancel(order: Order) -> Order:
    canceled, _refund = OrderService.cancel_order(
        order,
        reason="out of stock",
        refund_payment=False,
        customer_initiated=False,
    )
    return canceled


def _metadata(order_id: int) -> dict[str, Any]:
    return Order.objects.values_list("metadata", flat=True).get(pk=order_id)


def _is_canceled(shipment) -> bool:
    shipment.refresh_from_db()
    if isinstance(shipment, AcsShipment):
        return shipment.shipment_state == AcsShipmentState.CANCELED
    return shipment.parcel_state == BoxNowParcelState.CANCELED


@pytest.mark.parametrize("provider_code", ["acs", "boxnow"])
class TestTheCourierIsAskedAfterTheCancelCommitted:
    def test_a_staff_cancel_holds_no_lock_while_the_courier_answers(
        self, monkeypatch, provider_code
    ):
        shipment = _shipment(provider_code)
        courier = _courier(monkeypatch, shipment)

        canceled = _staff_cancel(shipment.order)

        assert courier.calls == [UNLOCKED]
        assert _is_canceled(shipment)
        # The caller still reads the courier's answer on the order it
        # gets back, and the database holds the same.
        assert canceled.metadata["cancellation"]["shipment_cancel"] == ASKED
        metadata = _metadata(shipment.order_id)
        assert metadata["cancellation"]["shipment_cancel"] == ASKED
        assert SHIPMENT_CANCEL_PENDING_FLAG not in metadata

    def test_a_cancel_inside_the_callers_transaction_waits_for_its_commit(
        self, monkeypatch, provider_code
    ):
        """The auto-cancel sweep cancels under its own row lock: the
        courier is asked once that transaction is over, not inside it."""
        shipment = _shipment(provider_code)
        courier = _courier(monkeypatch, shipment)

        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=shipment.order_id)
            _staff_cancel(locked)
            assert courier.calls == []

        assert courier.calls == [UNLOCKED]
        assert _is_canceled(shipment)
        metadata = _metadata(shipment.order_id)
        assert metadata["cancellation"]["shipment_cancel"] == ASKED
        assert SHIPMENT_CANCEL_PENDING_FLAG not in metadata

    def test_a_courier_that_gives_no_answer_leaves_the_order_canceled(
        self, monkeypatch, provider_code
    ):
        shipment = _shipment(provider_code)
        courier = _courier(
            monkeypatch, shipment, error=CarrierUnavailableError("timed out")
        )

        canceled = _staff_cancel(shipment.order)

        assert courier.calls == [UNLOCKED]
        assert canceled.status == OrderStatus.CANCELED
        metadata = _metadata(shipment.order_id)
        outcome = metadata["cancellation"]["shipment_cancel"]
        assert outcome["dispatched"] is False
        assert "timed out" in outcome["error"]
        # The answer is recorded, so nothing is left for the sweep.
        assert SHIPMENT_CANCEL_PENDING_FLAG not in metadata
        assert OrderHistory.objects.filter(
            order_id=shipment.order_id,
            change_type="NOTE",
            new_value__note__icontains="did not cancel the parcel",
        ).exists()

    def test_the_answer_is_written_beside_what_changed_meanwhile(
        self, monkeypatch, provider_code
    ):
        """No lock is held while the courier answers, so the order can
        change: the answer is merged into the row as it is then."""
        shipment = _shipment(provider_code)
        courier = _courier(monkeypatch, shipment)

        def another_writer() -> None:
            metadata = _metadata(shipment.order_id)
            Order.objects.filter(pk=shipment.order_id).update(
                metadata={**metadata, "written_meanwhile": True}
            )

        courier.during_call = lambda: _on_another_connection(another_writer)

        _staff_cancel(shipment.order)

        metadata = _metadata(shipment.order_id)
        assert metadata["written_meanwhile"] is True
        assert metadata["cancellation"]["shipment_cancel"] == ASKED


def test_the_auto_cancel_sweep_asks_the_courier_after_its_own_lock(
    monkeypatch,
):
    """``auto_cancel_stuck_pending_orders`` cancels each order inside a
    transaction that holds its row lock."""
    order = courier_cash_order(
        pay_way=PayWayFactory.create_online_payment("viva_wallet"),
        shipping_provider=ShippingProvider.objects.get(code="acs"),
        shipping_kind="home_delivery",
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
    )
    Order.objects.filter(pk=order.pk).update(
        payment_hold_expires_at=timezone.now() - timedelta(hours=2)
    )
    shipment = AcsShipmentFactory(order=order, with_voucher=True)
    courier = _courier(monkeypatch, shipment)

    result = auto_cancel_stuck_pending_orders()

    assert result["canceled_pending"] == 1, result
    assert courier.calls == [UNLOCKED]
    assert _is_canceled(shipment)
    metadata = _metadata(order.pk)
    assert metadata["cancellation"]["shipment_cancel"] == ASKED
    assert SHIPMENT_CANCEL_PENDING_FLAG not in metadata


def _age_the_cancel(order_id: int) -> None:
    Order.objects.filter(pk=order_id).update(
        status_updated_at=timezone.now()
        - timedelta(seconds=settings.ORDER_SHIPMENT_CANCEL_SWEEP_AFTER_SECONDS)
        - timedelta(seconds=1)
    )


@pytest.mark.parametrize("provider_code", ["acs", "boxnow"])
class TestACancelWhoseWorkerStopped:
    """The cancel committed and the process died before the courier was
    asked: the order carries the intent, and the sweep finishes it."""

    @staticmethod
    def _canceled_and_stopped(monkeypatch, provider_code):
        shipment = _shipment(provider_code)
        courier = _courier(monkeypatch, shipment)
        with monkeypatch.context() as stopped:
            stopped.setattr(
                OrderService,
                "cancel_attached_shipment",
                lambda *args, **kwargs: None,
            )
            _staff_cancel(shipment.order)
        return shipment, courier

    def test_the_order_is_canceled_and_says_the_courier_is_still_to_ask(
        self, monkeypatch, provider_code
    ):
        shipment, courier = self._canceled_and_stopped(
            monkeypatch, provider_code
        )

        assert courier.calls == []
        assert _committed_status(shipment.order_id) == OrderStatus.CANCELED
        metadata = _metadata(shipment.order_id)
        assert metadata[SHIPMENT_CANCEL_PENDING_FLAG] is True
        assert "shipment_cancel" not in metadata["cancellation"]
        assert not _is_canceled(shipment)

    def test_the_sweep_leaves_a_cancel_that_may_still_be_running(
        self, monkeypatch, provider_code
    ):
        shipment, courier = self._canceled_and_stopped(
            monkeypatch, provider_code
        )

        assert finish_pending_shipment_cancels() == NOTHING_PENDING

        assert courier.calls == []
        assert not _is_canceled(shipment)

    def test_the_sweep_asks_the_courier_and_records_the_answer(
        self, monkeypatch, provider_code
    ):
        shipment, courier = self._canceled_and_stopped(
            monkeypatch, provider_code
        )
        _age_the_cancel(shipment.order_id)

        assert finish_pending_shipment_cancels() == ONE_FINISHED

        assert courier.calls == [UNLOCKED]
        assert _is_canceled(shipment)
        metadata = _metadata(shipment.order_id)
        assert metadata["cancellation"]["shipment_cancel"] == ASKED
        assert SHIPMENT_CANCEL_PENDING_FLAG not in metadata
        # Finished: the next run has nothing to do.
        assert finish_pending_shipment_cancels() == NOTHING_PENDING
        assert len(courier.calls) == 1


def _courier_notes(order_id: int) -> list[str]:
    return [
        row.new_value["note"]
        for row in OrderHistory.objects.filter(
            order_id=order_id, change_type="NOTE"
        )
        if "did not cancel the parcel" in row.new_value["note"]
    ]


class TestAShipmentAlreadyBeingWorkedOnIsNotAnAnswer:
    """``CarrierBusyError`` is our own refusal, with no call to the
    courier: recorded as the courier's answer it would stand, with a
    staff note, while the cancel that is running succeeds."""

    def test_the_sweep_beside_a_cancel_still_waiting_for_acs(self, monkeypatch):
        shipment = _shipment("acs")
        order_id = shipment.order_id
        courier = _courier(monkeypatch, shipment)
        second: list[dict[str, int]] = []

        def the_sweep_runs_meanwhile() -> None:
            _age_the_cancel(order_id)
            second.append(finish_pending_shipment_cancels())

        courier.during_call = lambda: _on_another_connection(
            the_sweep_runs_meanwhile
        )

        canceled = _staff_cancel(shipment.order)

        # The sweep was refused by the first cancel's claim and wrote
        # nothing; the first cancel's answer is the one on the order.
        assert second == [ONE_STILL_PENDING]
        assert len(courier.calls) == 1
        assert canceled.metadata["cancellation"]["shipment_cancel"] == ASKED
        metadata = _metadata(order_id)
        assert metadata["cancellation"]["shipment_cancel"] == ASKED
        assert SHIPMENT_CANCEL_PENDING_FLAG not in metadata
        assert _courier_notes(order_id) == []
        assert _is_canceled(shipment)

    def test_a_boxnow_creation_in_flight_keeps_the_cancel_pending(
        self, monkeypatch
    ):
        shipment = BoxNowShipmentFactory(
            order=_order("boxnow"),
            metadata={"mint_started_at": timezone.now().isoformat()},
        )
        order_id = shipment.order_id
        courier = _courier(monkeypatch, shipment)

        canceled = _staff_cancel(shipment.order)

        assert canceled.status == OrderStatus.CANCELED
        assert courier.calls == []
        metadata = _metadata(order_id)
        assert metadata[SHIPMENT_CANCEL_PENDING_FLAG] is True
        assert "shipment_cancel" not in metadata["cancellation"]
        assert _courier_notes(order_id) == []

        # Still in flight on the next run: still not an answer.
        _age_the_cancel(order_id)
        assert finish_pending_shipment_cancels() == ONE_STILL_PENDING
        assert "shipment_cancel" not in _metadata(order_id)["cancellation"]

        # The creation ends and finds the order canceled: it records the
        # shipment CANCELED (``_persist_parcel``). The sweep's cancel is
        # then a no-op, and that is the answer.
        type(shipment).objects.filter(pk=shipment.pk).update(
            parcel_state=BoxNowParcelState.CANCELED
        )
        assert finish_pending_shipment_cancels() == ONE_FINISHED
        metadata = _metadata(order_id)
        assert metadata["cancellation"]["shipment_cancel"] == ASKED
        assert SHIPMENT_CANCEL_PENDING_FLAG not in metadata
        assert _courier_notes(order_id) == []


def test_a_flag_left_beside_an_answer_is_removed_without_asking_again(
    monkeypatch,
):
    """Only a writer that puts back a copy of the metadata loaded before
    the answer can leave this; the sweep must not take it forever."""
    shipment = _shipment("acs")
    order_id = shipment.order_id
    courier = _courier(monkeypatch, shipment)
    _staff_cancel(shipment.order)
    assert len(courier.calls) == 1
    Order.objects.filter(pk=order_id).update(
        metadata={**_metadata(order_id), SHIPMENT_CANCEL_PENDING_FLAG: True}
    )
    _age_the_cancel(order_id)

    assert finish_pending_shipment_cancels() == ONE_FINISHED

    assert len(courier.calls) == 1
    metadata = _metadata(order_id)
    assert metadata["cancellation"]["shipment_cancel"] == ASKED
    assert SHIPMENT_CANCEL_PENDING_FLAG not in metadata
    assert finish_pending_shipment_cancels() == NOTHING_PENDING


def _wait_for_a_blocked_backend() -> None:
    """Return once some connection of this database waits for a lock."""
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone()[0]:
                return
        time.sleep(0.05)
    pytest.fail("no connection waited for a row lock")


def _in_thread(run, errors: list[BaseException]) -> threading.Thread:
    def target() -> None:
        try:
            run()
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=target)
    thread.start()
    return thread


def test_an_acs_tracking_poll_and_a_staff_cancel_do_not_deadlock(monkeypatch):
    """#613. The cancel holds the order; the poll, which found the parcel
    moving, has to write the order too. The poll used to lock the shipment
    first and the cancel then asked for it under the order lock, so each
    waited for the other until PostgreSQL aborted one."""
    shipment = _shipment("acs")
    order_id = shipment.order_id
    courier = _courier(monkeypatch, shipment)

    order_is_locked = threading.Event()
    go_on = threading.Event()
    lock_products = StockManager.lock_products

    def hold_the_order_lock(product_ids) -> None:
        # The first thing a cancel does once it holds the order row.
        order_is_locked.set()
        assert go_on.wait(WAIT)
        lock_products(product_ids)

    monkeypatch.setattr(StockManager, "lock_products", hold_the_order_lock)

    errors: list[BaseException] = []
    cancel = _in_thread(
        lambda: _staff_cancel(Order.objects.get(pk=order_id)), errors
    )
    assert order_is_locked.wait(WAIT)
    poll = _in_thread(
        lambda: AcsService.poll_shipment_tracking(
            AcsShipment.objects.get(pk=shipment.pk)
        ),
        errors,
    )
    _wait_for_a_blocked_backend()
    go_on.set()
    cancel.join(WAIT)
    poll.join(WAIT)

    assert not cancel.is_alive()
    assert not poll.is_alive()
    assert errors == []
    assert courier.calls == [UNLOCKED]
    assert _committed_status(order_id) == OrderStatus.CANCELED
    assert _is_canceled(shipment)
    assert _metadata(order_id)["cancellation"]["shipment_cancel"] == ASKED
