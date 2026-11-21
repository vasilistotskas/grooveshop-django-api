"""Every Conversions API task sends one event per dispatch.

A task queued through ``dispatch_on_commit`` can run more than once (a
retry, a message delivered again, the sweep's copy of a publish), and Meta's
own deduplication of a server event sent twice is not something to count
on. Each event's id is fixed before the task runs again: the order's or the
account's number, or for a Refund the dispatch's task id. ``MetaCapiClient``
is the only thing mocked.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from unittest.mock import patch

import pytest

from meta_capi.client import MetaCapiResponse
from meta_capi.models import MetaCapiEventLog, MetaCapiEventStatus
from meta_capi.tasks import (
    dispatch_complete_registration_event,
    dispatch_initiate_checkout_event,
    dispatch_purchase_event,
    dispatch_refund_event,
)
from order.factories.order import OrderFactory
from tests.utils.concurrency import in_thread, wait_for_a_blocked_backend
from tests.utils.dispatch import apply_in
from user.factories.account import UserAccountFactory

WAIT = 10
#: Consent and no browser-minted id: the server picks every id.
CONSENTED = {"meta": {"consent": {"ads": True}}}


@pytest.fixture
def meta_send():
    with patch("meta_capi.tasks.MetaCapiClient") as client_cls:
        send = client_cls.return_value.send
        send.return_value = MetaCapiResponse(
            events_received=1, fbtrace_id="trace-abc", messages=[]
        )
        yield send


def _order_id() -> int:
    # A fixed E.164 number: the SDK's normaliser rejects Faker's extensions.
    return OrderFactory(
        phone="+302101234567", metadata=CONSENTED, num_order_items=1
    ).id


def _purchase():
    return dispatch_purchase_event, [_order_id()]


def _initiate_checkout():
    return dispatch_initiate_checkout_event, [_order_id()]


def _refund():
    return dispatch_refund_event, [_order_id(), "12.50"]


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
    task, args = build()

    apply_in(capi_store, task, args, task_id="one-dispatch")
    apply_in(capi_store, task, args, task_id="one-dispatch")

    meta_send.assert_called_once()
    [row] = MetaCapiEventLog.objects.all()
    assert row.status == MetaCapiEventStatus.SENT


@pytest.mark.django_db
def test_another_refund_is_another_event(capi_store, meta_send):
    """Two refunds of one order are two dispatches, each its own event."""
    task, args = _refund()

    apply_in(capi_store, task, args, task_id="first-refund")
    apply_in(capi_store, task, args, task_id="second-refund")

    assert meta_send.call_count == 2
    assert MetaCapiEventLog.objects.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "build",
    [_purchase, _initiate_checkout, _complete_registration],
    ids=["purchase", "initiate checkout", "complete registration"],
)
def test_one_order_or_account_is_one_event_whatever_the_dispatch(
    build, capi_store, meta_send
):
    task, args = build()

    apply_in(capi_store, task, args, task_id="one-dispatch")
    apply_in(capi_store, task, args, task_id="another-dispatch")

    meta_send.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_two_copies_at_the_same_moment_send_once(capi_store, meta_send):
    """Each copy on its own thread and connection, as two workers
    (``tests.utils.concurrency``): the first is held inside the send, the
    second waits on the event's lock and then finds the row SENT."""
    task, args = _purchase()
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
            apply_in(capi_store, task, args, task_id="one-dispatch")

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
