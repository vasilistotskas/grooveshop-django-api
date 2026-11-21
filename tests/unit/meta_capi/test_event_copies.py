"""Every Conversions API task sends one event per dispatch.

A task queued through ``dispatch_on_commit`` can run more than once (a
retry, a message delivered again, the sweep's copy of a publish), and Meta's
own deduplication of a server event sent twice is not something to count
on. Each event's id is fixed before the task runs again: the order's or the
account's number, or for a Refund its ledger row. ``MetaCapiClient`` is the
only thing mocked.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from celery.exceptions import Retry
from django.db import connection

from core.db.locks import held_across_calls
from meta_capi.models import MetaCapiEventLog, MetaCapiEventStatus
from meta_capi.tasks import (
    dispatch_complete_registration_event,
    dispatch_initiate_checkout_event,
    dispatch_purchase_event,
    dispatch_refund_event,
)
from order.factories.payment_transaction import RefundTransactionFactory
from tests.unit.meta_capi.conftest import capi_order
from tests.utils.concurrency import in_thread, wait_for_a_blocked_backend
from tests.utils.dispatch import apply_in
from user.factories.account import UserAccountFactory

WAIT = 10
#: Consent and no browser-minted id: the server picks every id.
CONSENTED = {"meta": {"consent": {"ads": True}}}


def _order_id() -> int:
    return capi_order(metadata=CONSENTED, num_order_items=1).id


def _purchase():
    return dispatch_purchase_event, [_order_id()]


def _initiate_checkout():
    return dispatch_initiate_checkout_event, [_order_id()]


def _refund(order_id: int | None = None):
    order_id = order_id or _order_id()
    refund = RefundTransactionFactory(order_id=order_id)
    return dispatch_refund_event, [order_id, "12.50"], {"refund_id": refund.pk}


def _complete_registration():
    return dispatch_complete_registration_event, [UserAccountFactory().id]


EVENTS: dict[str, Callable[[], tuple]] = {
    "purchase": _purchase,
    "initiate checkout": _initiate_checkout,
    "refund": _refund,
    "complete registration": _complete_registration,
}


@pytest.mark.django_db
@pytest.mark.parametrize("build", EVENTS.values(), ids=EVENTS.keys())
def test_a_second_copy_of_a_dispatch_sends_nothing(
    build, capi_store, meta_send
):
    task, *call = build()

    apply_in(capi_store, task, *call, task_id="one-dispatch")
    apply_in(capi_store, task, *call, task_id="one-dispatch")

    meta_send.assert_called_once()
    [row] = MetaCapiEventLog.objects.all()
    assert row.status == MetaCapiEventStatus.SENT


@pytest.mark.django_db
def test_another_refund_of_the_order_is_another_event(capi_store, meta_send):
    """Two REFUND rows of one order are two refunds, each its own event."""
    order_id = _order_id()
    task, *first = _refund(order_id)
    _, *second = _refund(order_id)

    apply_in(capi_store, task, *first, task_id="first-refund")
    apply_in(capi_store, task, *second, task_id="second-refund")

    assert meta_send.call_count == 2
    assert MetaCapiEventLog.objects.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("build", EVENTS.values(), ids=EVENTS.keys())
def test_one_order_account_or_refund_is_one_event_whatever_the_dispatch(
    build, capi_store, meta_send
):
    task, *call = build()

    apply_in(capi_store, task, *call, task_id="one-dispatch")
    apply_in(capi_store, task, *call, task_id="another-dispatch")

    meta_send.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_two_copies_at_the_same_moment_send_once(capi_store, meta_send):
    """Each copy on its own thread and connection, as two workers
    (``tests.utils.concurrency``): the first is held inside the send, the
    second waits on the event's lock and then finds the row SENT."""
    task, *call = _purchase()
    sending = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []
    answered = meta_send.return_value

    def held_send(events):
        sending.set()
        assert release.wait(WAIT)
        return answered

    meta_send.side_effect = held_send

    def copy():
        def run() -> None:
            apply_in(capi_store, task, *call, task_id="one-dispatch")

        return in_thread(run, failures)

    first = copy()
    assert sending.wait(WAIT)
    second = copy()
    wait_for_a_blocked_backend(WAIT)

    release.set()
    first.finish(WAIT)
    second.finish(WAIT)

    assert not failures, failures
    meta_send.assert_called_once()
    [row] = MetaCapiEventLog.objects.all()
    assert row.status == MetaCapiEventStatus.SENT


@pytest.mark.django_db(transaction=True)
def test_a_copy_whose_wait_runs_out_retries_and_sends_nothing(
    capi_store, meta_send
):
    """Another session holds the event's lock past this copy's
    ``lock_timeout``: the copy asks for a retry, as for a 5xx, and its
    next attempt finds the row SENT. Nothing is sent meanwhile."""
    task, *call = _purchase()
    holding = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []
    [order_id] = call[0]
    event_id = f"purchase-{order_id}"

    def holder() -> None:
        with held_across_calls(
            f"meta_capi.event:{capi_store.schema_name}:{event_id}"
        ):
            holding.set()
            assert release.wait(WAIT)

    held = in_thread(holder, failures)
    assert holding.wait(WAIT)
    with connection.cursor() as cursor:
        cursor.execute("SET lock_timeout = '200ms'")
    try:
        with pytest.raises(Retry):
            apply_in(capi_store, task, *call, task_id="one-dispatch")
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET lock_timeout")
        release.set()
        held.finish(WAIT)

    assert not failures, failures
    meta_send.assert_not_called()
    assert not MetaCapiEventLog.objects.exists()
