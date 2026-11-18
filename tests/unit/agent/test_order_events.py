"""The order events owed to the agent gateway (``agent.outbox``).

- Recording: an order an agent placed gets an "Order created" event in
  its creation transaction, and an "Order updated" event for each save
  that changed its status, payment status or tracking; nothing else does.
- Delivery: one attempt sends the row's id and event time, unchanged on
  every retry, and the gateway's answer decides the row's state.
- The sweep, the manual retry and the retention purge.
"""

from __future__ import annotations

import socketserver
import threading
import time
from datetime import timedelta
from email.utils import format_datetime
from unittest.mock import Mock, patch

import pytest
import requests
from django.conf import settings
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from agent import outbox
from agent.models import AgentOrderEvent
from agent.models import AgentOrderEventStatus as Status
from agent.models import AgentOrderEventType as EventType
from agent.tasks import purge_expired_agent_order_events
from order.enum.attribution import OrderSourceType
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.attribution import OrderAttribution
from order.models.order import Order
from settings.schedule import SCHEDULE_PRESETS
from tenant.models import Tenant
from tests.utils.staff import store_tenant

GATEWAY_URL = "http://agent-gateway-service"
SECRET = "test-internal-secret"
BACKOFF = 600
gateway = override_settings(
    AGENT_GATEWAY_INTERNAL_URL=GATEWAY_URL,
    AGENT_GATEWAY_INTERNAL_SECRET=SECRET,
    AGENT_ORDER_EVENT_BACKOFF_SECONDS=BACKOFF,
    AGENT_ORDER_EVENT_BACKOFF_JITTER=0,
)


#: The store every test runs as unless it binds another.
STORE = "outbox_store"


def _the_store() -> Tenant:
    """The active store's row, made on first use inside the test's own
    transaction, so it goes with the test."""
    return Tenant.objects.filter(schema_name=STORE).first() or store_tenant(
        STORE
    )


@pytest.fixture(autouse=True)
def _an_active_store():
    """The suite runs in the public schema with no store bound; the outbox
    attempts only for a store the gateway can resolve
    (``outbox._store_is_active``), so every test runs as an active one
    unless it binds another."""
    with patch("agent.outbox.bound_tenant", side_effect=_the_store):
        yield


def _response(status_code: int, **headers: str) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.headers = {
        name.replace("_", "-"): value for name, value in headers.items()
    }
    return response


def _order(source_type: str | None = OrderSourceType.AGENT) -> Order:
    order = OrderFactory(
        status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
    )
    order.tracking_number = ""
    order.shipping_carrier = ""
    order.save()
    if source_type is not None:
        OrderAttribution.objects.create(
            order=order, source_type=source_type, source="ucp"
        )
    # Re-read so the _original_* snapshots match the row.
    return Order.objects.get(pk=order.pk)


@gateway
@patch("agent.tasks.deliver_agent_order_event")
class RecordTests(TestCase):
    def events(self, order: Order) -> list[str]:
        return list(
            AgentOrderEvent.objects.filter(order=order)
            .order_by("occurred_at", "pk")
            .values_list("event_type", flat=True)
        )

    def test_an_agent_order_records_order_created(self, task):
        order = _order()

        event = AgentOrderEvent.objects.get(order=order)
        self.assertEqual(event.event_type, EventType.CREATED)
        self.assertEqual(event.status, Status.PENDING)
        self.assertEqual(event.attempts, 0)
        task.apply_async.assert_called_once_with(
            args=[str(event.pk)],
            headers={"_schema_name": connection.schema_name},
        )

    def test_each_tracked_change_records_one_update(self, task):
        changes = {
            "status": {"status": OrderStatus.PROCESSING},
            "payment status": {"payment_status": PaymentStatus.COMPLETED},
            "tracking": {
                "tracking_number": "ABC123",
                "shipping_carrier": "ELTA",
            },
            "status and payment together": {
                "status": OrderStatus.PROCESSING,
                "payment_status": PaymentStatus.COMPLETED,
            },
        }
        for name, fields in changes.items():
            with self.subTest(name):
                order = _order()
                for field, value in fields.items():
                    setattr(order, field, value)
                order.save()

                self.assertEqual(
                    self.events(order), [EventType.CREATED, EventType.UPDATED]
                )

    def test_an_unrelated_save_records_nothing(self, task):
        order = _order()

        order.customer_notes = "please ring the bell"
        order.save()

        self.assertEqual(self.events(order), [EventType.CREATED])

    def test_an_order_no_agent_placed_records_nothing(self, task):
        for source_type in (OrderSourceType.DIRECT, None):
            with self.subTest(source_type=source_type):
                order = _order(source_type)

                order.status = OrderStatus.PROCESSING
                order.save()

                self.assertEqual(self.events(order), [])

    @override_settings(AGENT_GATEWAY_INTERNAL_URL="")
    def test_nothing_is_recorded_without_the_gateway(self, task):
        order = _order()

        order.status = OrderStatus.PROCESSING
        order.save()

        self.assertEqual(self.events(order), [])
        task.apply_async.assert_not_called()

    def test_a_change_that_rolls_back_leaves_no_event(self, task):
        order = _order()

        with self.assertRaises(RuntimeError), transaction.atomic():
            order.status = OrderStatus.PROCESSING
            order.save()
            raise RuntimeError

        self.assertEqual(self.events(order), [EventType.CREATED])


@gateway
@patch("agent.outbox.requests.post")
class AttemptTests(TestCase):
    def setUp(self):
        with override_settings(AGENT_GATEWAY_INTERNAL_URL=""):
            self.order = _order()

    def event(self, **fields) -> AgentOrderEvent:
        return AgentOrderEvent.objects.create(
            order=self.order, event_type=EventType.UPDATED, **fields
        )

    def make_due(self, event: AgentOrderEvent) -> None:
        AgentOrderEvent.objects.filter(pk=event.pk).update(
            next_attempt_at=timezone.now()
        )

    def test_a_delivered_event_is_finished(self, post):
        post.return_value = _response(204)
        event = self.event()

        outcome = outbox.attempt(event.pk)

        self.assertEqual(outcome, outbox.Outcome.DELIVERED)
        event.refresh_from_db()
        self.assertEqual(event.status, Status.DELIVERED)
        self.assertEqual(event.attempts, 1)
        self.assertIsNotNone(event.finished_at)
        args, kwargs = post.call_args
        self.assertEqual(args[0], f"{GATEWAY_URL}/internal/events/order-status")
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": SECRET})
        self.assertEqual(
            kwargs["json"],
            {
                "eventId": str(event.pk),
                "eventType": EventType.UPDATED.value,
                "occurredAt": event.occurred_at.isoformat(),
                "schemaName": connection.schema_name,
                "orderUuid": str(self.order.uuid),
                "status": OrderStatus.PENDING.value,
                "paymentStatus": PaymentStatus.PENDING.value,
                "trackingNumber": "",
            },
        )

    def test_a_retry_sends_the_same_id_and_time(self, post):
        post.side_effect = [_response(503), _response(204)]
        event = self.event()

        first = outbox.attempt(event.pk)
        event.refresh_from_db()
        self.assertEqual(first, outbox.Outcome.RETRY)
        self.assertEqual(event.status, Status.PENDING)
        self.assertEqual(event.last_error, "HTTP 503")
        self.assertEqual(
            event.next_attempt_at - event.last_attempt_at,
            timedelta(seconds=BACKOFF),
        )
        self.make_due(event)
        second = outbox.attempt(event.pk)

        self.assertEqual(second, outbox.Outcome.DELIVERED)
        bodies = [call.kwargs["json"] for call in post.call_args_list]
        self.assertEqual(
            {(body["eventId"], body["occurredAt"]) for body in bodies},
            {(str(event.pk), event.occurred_at.isoformat())},
        )

    def test_the_gateways_answer_decides_the_state(self, post):
        answers = {
            400: (outbox.Outcome.FAILED, Status.FAILED),
            422: (outbox.Outcome.FAILED, Status.FAILED),
            401: (outbox.Outcome.MISCONFIGURED, Status.PENDING),
            403: (outbox.Outcome.MISCONFIGURED, Status.PENDING),
            404: (outbox.Outcome.MISCONFIGURED, Status.PENDING),
            408: (outbox.Outcome.RETRY, Status.PENDING),
            429: (outbox.Outcome.RETRY, Status.PENDING),
            500: (outbox.Outcome.RETRY, Status.PENDING),
            503: (outbox.Outcome.RETRY, Status.PENDING),
        }
        for code, (outcome, state) in answers.items():
            with self.subTest(code=code):
                post.return_value = _response(code)
                event = self.event()

                self.assertEqual(outbox.attempt(event.pk), outcome)
                event.refresh_from_db()
                self.assertEqual(event.status, state)
                self.assertEqual(event.last_error, f"HTTP {code}")
                event.delete()

    def test_a_deployment_fault_is_retried_on_the_backoff_and_logged(
        self, post
    ):
        post.return_value = _response(403)
        event = self.event()

        with self.assertLogs("agent.outbox", level="ERROR") as logs:
            outcome = outbox.attempt(event.pk)

        self.assertEqual(outcome, outbox.Outcome.MISCONFIGURED)
        event.refresh_from_db()
        self.assertEqual(event.status, Status.PENDING)
        # One attempt spent, the next on the ordinary backoff.
        self.assertEqual(event.attempts, 1)
        self.assertEqual(
            event.next_attempt_at - event.last_attempt_at,
            timedelta(seconds=BACKOFF),
        )
        self.assertIn("HTTP 403", logs.output[0])

    def test_retry_after_sets_the_next_attempt_within_its_bounds(self, post):
        floor = settings.AGENT_ORDER_EVENT_RETRY_AFTER_MIN_SECONDS
        cap = settings.AGENT_ORDER_EVENT_RETRY_AFTER_CAP_SECONDS
        in_two_minutes = format_datetime(
            timezone.now() + timedelta(minutes=2), usegmt=True
        )
        waits = {
            "45": 45,
            "1": floor,
            str(cap * 10): cap,
            in_two_minutes: 120,
            "soon": BACKOFF,
        }
        for header, seconds in waits.items():
            with self.subTest(retry_after=header):
                post.return_value = _response(503, Retry_After=header)
                event = self.event()

                before = timezone.now()
                outbox.attempt(event.pk)
                event.refresh_from_db()

                wait = (event.next_attempt_at - before).total_seconds()
                self.assertAlmostEqual(wait, seconds, delta=3)
                event.delete()

    def test_a_call_waits_the_outbox_timeouts(self, post):
        post.return_value = _response(204)
        event = self.event()

        outbox.attempt(event.pk)

        self.assertEqual(
            post.call_args.kwargs["timeout"],
            (
                settings.AGENT_ORDER_EVENT_CONNECT_TIMEOUT,
                settings.AGENT_ORDER_EVENT_READ_TIMEOUT,
            ),
        )

    def test_a_soft_deleted_orders_event_fails_at_once(self, post):
        event = self.event()
        self.order.delete()
        self.assertFalse(Order.objects.filter(pk=self.order.pk).exists())

        self.assertEqual(outbox.attempt(event.pk), outbox.Outcome.FAILED)

        post.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.status, Status.FAILED)
        self.assertEqual(event.last_error, outbox.ORDER_DELETED)
        self.assertIsNotNone(event.finished_at)

    def test_an_order_deleted_for_good_fails_its_event_at_once(self, post):
        event = self.event()

        with patch("agent.outbox._order_state", return_value=None):
            outcome = outbox.attempt(event.pk)

        self.assertEqual(outcome, outbox.Outcome.FAILED)
        post.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.status, Status.FAILED)
        self.assertEqual(event.last_error, outbox.ORDER_DELETED)

    def test_a_gateway_fault_on_the_last_attempt_fails_the_event_and_stops(
        self, post
    ):
        last = settings.AGENT_ORDER_EVENT_MAX_ATTEMPTS - 1
        for side_effect, outcome in (
            (requests.ConnectionError("refused"), outbox.Outcome.UNREACHABLE),
            (_response(403), outbox.Outcome.MISCONFIGURED),
        ):
            with self.subTest(outcome=outcome):
                if isinstance(side_effect, Exception):
                    post.side_effect = side_effect
                else:
                    post.side_effect = None
                    post.return_value = side_effect
                event = self.event(attempts=last)

                self.assertEqual(outbox.attempt(event.pk), outcome)
                event.refresh_from_db()
                self.assertEqual(event.status, Status.FAILED)
                event.delete()

    def test_an_attempt_a_manual_retry_superseded_writes_nothing(self, post):
        # The last attempt is in flight when staff send the event again:
        # its failure must not end the fresh cycle.
        event = self.event(attempts=settings.AGENT_ORDER_EVENT_MAX_ATTEMPTS - 1)

        def answer_after_a_manual_retry(body):
            outbox.retry_now(AgentOrderEvent.objects.filter(pk=event.pk))
            return 503, {}

        with (
            patch("agent.tasks.deliver_agent_order_event"),
            patch(
                "agent.outbox._post", side_effect=answer_after_a_manual_retry
            ),
        ):
            outcome = outbox.attempt(event.pk)

        self.assertEqual(outcome, outbox.Outcome.SKIPPED)
        event.refresh_from_db()
        self.assertEqual(event.status, Status.PENDING)
        self.assertEqual(event.attempts, 0)
        self.assertIsNone(event.finished_at)
        self.assertIsNone(event.claim)
        self.assertEqual(event.last_error, "")

    def test_an_unreachable_gateway_is_attempted_again(self, post):
        post.side_effect = requests.ConnectionError("refused")
        event = self.event()

        self.assertEqual(outbox.attempt(event.pk), outbox.Outcome.UNREACHABLE)
        event.refresh_from_db()
        self.assertEqual(event.status, Status.PENDING)
        self.assertEqual(event.last_error, "ConnectionError")

    def test_the_last_failed_attempt_gives_up(self, post):
        post.return_value = _response(503)
        event = self.event(attempts=settings.AGENT_ORDER_EVENT_MAX_ATTEMPTS - 1)

        self.assertEqual(outbox.attempt(event.pk), outbox.Outcome.FAILED)
        event.refresh_from_db()
        self.assertEqual(event.status, Status.FAILED)
        self.assertEqual(
            event.attempts, settings.AGENT_ORDER_EVENT_MAX_ATTEMPTS
        )
        self.assertIsNotNone(event.finished_at)

    def test_a_row_left_after_its_last_attempt_is_not_sent_again(self, post):
        # A worker that died during the last attempt leaves it pending.
        event = self.event(attempts=settings.AGENT_ORDER_EVENT_MAX_ATTEMPTS)

        self.assertEqual(outbox.attempt(event.pk), outbox.Outcome.FAILED)
        post.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.status, Status.FAILED)

    def test_an_event_not_due_is_not_sent(self, post):
        event = self.event(next_attempt_at=timezone.now() + timedelta(hours=1))

        self.assertEqual(outbox.attempt(event.pk), outbox.Outcome.SKIPPED)
        post.assert_not_called()

    def test_a_claimed_event_is_not_sent_twice(self, post):
        post.return_value = _response(503)
        event = self.event()

        outbox.attempt(event.pk)
        again = outbox.attempt(event.pk)

        self.assertEqual(again, outbox.Outcome.SKIPPED)
        post.assert_called_once()

    def test_an_event_waits_behind_an_earlier_one_of_its_order(self, post):
        post.return_value = _response(204)
        earlier = self.event()
        later = self.event()

        self.assertEqual(outbox.attempt(later.pk), outbox.Outcome.SKIPPED)
        outbox.attempt(earlier.pk)
        self.assertEqual(outbox.attempt(later.pk), outbox.Outcome.DELIVERED)

    def test_a_failed_earlier_event_holds_nothing_back(self, post):
        post.return_value = _response(204)
        self.event(status=Status.FAILED)
        later = self.event()

        self.assertEqual(outbox.attempt(later.pk), outbox.Outcome.DELIVERED)


class ScheduleTests(TestCase):
    @override_settings(AGENT_ORDER_EVENT_BACKOFF_JITTER=0)
    def test_about_ten_attempts_over_about_three_days(self):
        attempts = settings.AGENT_ORDER_EVENT_MAX_ATTEMPTS
        span = sum(
            (outbox.retry_delay(number) for number in range(1, attempts)),
            timedelta(),
        )

        self.assertEqual(attempts, 10)
        self.assertGreater(span, timedelta(days=2.5))
        self.assertLess(span, timedelta(days=3.5))

    def test_jitter_stays_within_its_fraction(self):
        jitter = settings.AGENT_ORDER_EVENT_BACKOFF_JITTER
        base = settings.AGENT_ORDER_EVENT_BACKOFF_SECONDS

        for _ in range(50):
            seconds = outbox.retry_delay(1).total_seconds()
            self.assertGreaterEqual(seconds, base * (1 - jitter))
            self.assertLessEqual(seconds, base * (1 + jitter))


@gateway
@patch("agent.outbox.requests.post")
class SweepTests(TestCase):
    def setUp(self):
        with override_settings(AGENT_GATEWAY_INTERNAL_URL=""):
            self.orders = [_order(), _order()]

    def event(self, order: Order, **fields) -> AgentOrderEvent:
        return AgentOrderEvent.objects.create(
            order=order, event_type=EventType.UPDATED, **fields
        )

    def test_an_orders_events_go_out_in_order_in_one_run(self, post):
        post.return_value = _response(204)
        first = self.event(self.orders[0])
        second = self.event(self.orders[0])

        counts = outbox.deliver_due()

        self.assertEqual(counts, {outbox.Outcome.DELIVERED: 2})
        sent = [call.kwargs["json"]["eventId"] for call in post.call_args_list]
        self.assertEqual(sent, [str(first.pk), str(second.pk)])

    def test_the_run_stops_when_the_gateway_is_unreachable(self, post):
        post.side_effect = requests.ConnectionError("refused")
        for order in self.orders:
            self.event(order)

        counts = outbox.deliver_due()

        self.assertEqual(counts, {outbox.Outcome.UNREACHABLE: 1})
        post.assert_called_once()

    def test_a_suspended_stores_events_wait_until_it_is_reactivated(self, post):
        post.return_value = _response(204)
        event = self.event(self.orders[0])
        store = store_tenant("outbox_suspended", is_active=False)

        with patch("agent.outbox.bound_tenant", return_value=store):
            self.assertEqual(
                outbox.deliver_due(), {outbox.Outcome.STORE_INACTIVE: 1}
            )
            self.assertEqual(
                outbox.attempt(event.pk), outbox.Outcome.STORE_INACTIVE
            )
            post.assert_not_called()
            event.refresh_from_db()
            self.assertEqual(event.status, Status.PENDING)
            self.assertEqual(event.attempts, 0)

            Tenant.objects.filter(pk=store.pk).update(is_active=True)
            self.assertEqual(
                outbox.deliver_due(), {outbox.Outcome.DELIVERED: 1}
            )

        event.refresh_from_db()
        self.assertEqual(event.status, Status.DELIVERED)

    def test_a_store_suspended_during_a_run_gets_no_further_attempt(self, post):
        first = self.event(self.orders[0])
        second = self.event(self.orders[1])

        post.return_value = _response(204)
        body = outbox._body
        # One row for the whole run, as a task binds its store once.
        store = _the_store()

        # Suspended once the first attempt has passed the check: the call
        # itself runs in a thread outside this test's transaction.
        def suspend_then_body(*args):
            Tenant.objects.filter(pk=store.pk).update(is_active=False)
            return body(*args)

        with (
            patch("agent.outbox.bound_tenant", return_value=store),
            patch("agent.outbox._body", side_effect=suspend_then_body),
        ):
            counts = outbox.deliver_due()

        self.assertEqual(
            counts,
            {
                outbox.Outcome.DELIVERED: 1,
                outbox.Outcome.STORE_INACTIVE: 1,
            },
        )
        post.assert_called_once()
        delivered, held = sorted(
            AgentOrderEvent.objects.filter(pk__in=[first.pk, second.pk]),
            key=lambda event: event.status != Status.DELIVERED,
        )
        self.assertEqual(delivered.status, Status.DELIVERED)
        self.assertEqual(held.status, Status.PENDING)
        self.assertEqual(held.attempts, 0)

    def test_a_store_still_active_with_a_leftover_suspension_is_retried(
        self, post
    ):
        post.side_effect = [_response(503), _response(204)]
        event = self.event(self.orders[0])
        store = store_tenant(
            "outbox_leftover_suspension",
            is_active=True,
            suspended_at=timezone.now(),
        )

        with patch("agent.outbox.bound_tenant", return_value=store):
            self.assertEqual(outbox.deliver_due(), {outbox.Outcome.RETRY: 1})
            AgentOrderEvent.objects.filter(pk=event.pk).update(
                next_attempt_at=timezone.now()
            )
            self.assertEqual(
                outbox.deliver_due(), {outbox.Outcome.DELIVERED: 1}
            )

        event.refresh_from_db()
        self.assertEqual(event.status, Status.DELIVERED)
        self.assertEqual(event.attempts, 2)

    def test_a_schema_with_no_store_row_sends_nothing(self, post):
        event = self.event(self.orders[0])

        with patch("agent.outbox.bound_tenant", return_value=None):
            self.assertEqual(
                outbox.deliver_due(), {outbox.Outcome.STORE_INACTIVE: 1}
            )
            self.assertEqual(
                outbox.attempt(event.pk), outbox.Outcome.STORE_INACTIVE
            )

        post.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.status, Status.PENDING)
        self.assertEqual(event.attempts, 0)

    def test_an_event_not_due_waits(self, post):
        self.event(
            self.orders[0], next_attempt_at=timezone.now() + timedelta(hours=1)
        )

        self.assertEqual(outbox.deliver_due(), {})
        post.assert_not_called()

    def test_the_run_stops_on_a_deployment_fault_and_logs_it_once(self, post):
        post.return_value = _response(401)
        for order in self.orders:
            self.event(order)

        with self.assertLogs("agent.outbox", level="ERROR") as logs:
            counts = outbox.deliver_due()

        self.assertEqual(counts, {outbox.Outcome.MISCONFIGURED: 1})
        post.assert_called_once()
        self.assertEqual(len(logs.records), 1)

    def test_a_run_takes_no_new_event_once_its_budget_is_spent(self, post):
        post.return_value = _response(204)
        for order in self.orders:
            self.event(order)
        budget = settings.AGENT_ORDER_EVENT_SWEEP_BUDGET_SECONDS
        # Start, the first check, then a check after a slow first call.
        clock = iter([0.0, 0.0, budget])

        with patch("agent.outbox.time.monotonic", side_effect=clock):
            counts = outbox.deliver_due()

        self.assertEqual(counts, {outbox.Outcome.DELIVERED: 1})

    def test_a_gateway_fault_that_ends_events_still_stops_the_run(self, post):
        post.side_effect = requests.ConnectionError("refused")
        for order in self.orders:
            self.event(
                order, attempts=settings.AGENT_ORDER_EVENT_MAX_ATTEMPTS - 1
            )

        with self.assertLogs("agent.outbox", level="ERROR") as logs:
            counts = outbox.deliver_due()

        self.assertEqual(counts, {outbox.Outcome.UNREACHABLE: 1})
        post.assert_called_once()
        self.assertEqual(len(logs.records), 1)

    @override_settings(AGENT_ORDER_EVENT_SWEEP_BATCH=1)
    def test_a_run_takes_at_most_a_batch(self, post):
        post.return_value = _response(204)
        for order in self.orders:
            self.event(order)

        self.assertEqual(outbox.deliver_due(), {outbox.Outcome.DELIVERED: 1})


@gateway
@patch("agent.tasks.deliver_agent_order_event")
class RetryNowTests(TestCase):
    def test_a_failed_event_is_queued_with_fresh_attempts(self, task):
        with override_settings(AGENT_GATEWAY_INTERNAL_URL=""):
            order = _order()
        failed = AgentOrderEvent.objects.create(
            order=order,
            event_type=EventType.UPDATED,
            status=Status.FAILED,
            attempts=10,
            finished_at=timezone.now(),
            next_attempt_at=timezone.now() + timedelta(days=1),
        )
        delivered = AgentOrderEvent.objects.create(
            order=order,
            event_type=EventType.CREATED,
            status=Status.DELIVERED,
            finished_at=timezone.now(),
        )

        queued = outbox.retry_now(AgentOrderEvent.objects.all())

        self.assertEqual(queued, 1)
        failed.refresh_from_db()
        delivered.refresh_from_db()
        self.assertEqual(failed.status, Status.PENDING)
        self.assertEqual(failed.attempts, 0)
        self.assertIsNone(failed.finished_at)
        self.assertLessEqual(failed.next_attempt_at, timezone.now())
        self.assertEqual(delivered.status, Status.DELIVERED)
        task.apply_async.assert_called_once_with(
            args=[str(failed.pk)],
            headers={"_schema_name": connection.schema_name},
        )

    def test_an_event_of_a_soft_deleted_order_fails_instead(self, task):
        with override_settings(AGENT_GATEWAY_INTERNAL_URL=""):
            order = _order()
        failed = AgentOrderEvent.objects.create(
            order=order,
            event_type=EventType.UPDATED,
            status=Status.FAILED,
            attempts=10,
            last_error="HTTP 503",
            finished_at=timezone.now() - timedelta(days=1),
        )
        pending = AgentOrderEvent.objects.create(
            order=order, event_type=EventType.UPDATED, attempts=2
        )
        order.delete()

        queued = outbox.retry_now(AgentOrderEvent.objects.all())

        self.assertEqual(queued, 0)
        task.apply_async.assert_not_called()
        for event in (failed, pending):
            with self.subTest(event=event.pk):
                event.refresh_from_db()
                self.assertEqual(event.status, Status.FAILED)
                self.assertEqual(event.last_error, outbox.ORDER_DELETED)
                self.assertIsNone(event.claim)
                self.assertGreater(
                    event.finished_at, timezone.now() - timedelta(minutes=1)
                )


class PurgeTests(TestCase):
    def test_finished_events_past_retention_are_deleted(self):
        order = _order(None)
        old = timezone.now() - timedelta(
            days=settings.AGENT_ORDER_EVENT_RETENTION_DAYS + 1
        )
        rows = {
            name: AgentOrderEvent.objects.create(
                order=order,
                event_type=EventType.UPDATED,
                status=state,
                finished_at=finished_at,
                occurred_at=old,
            )
            for name, state, finished_at in (
                ("old delivered", Status.DELIVERED, old),
                ("old failed", Status.FAILED, old),
                ("recent delivered", Status.DELIVERED, timezone.now()),
                ("old pending", Status.PENDING, None),
            )
        }

        result = purge_expired_agent_order_events()

        self.assertEqual(result, {"deleted": 2})
        kept = set(AgentOrderEvent.objects.values_list("pk", flat=True))
        self.assertEqual(
            kept, {rows["recent delivered"].pk, rows["old pending"].pk}
        )


def test_a_sweep_run_ends_before_the_next_one_starts():
    """A run takes no new event after its budget, and the event it took
    last can wait the whole connect and read timeouts: together they stay
    under the minute between runs and the task's time limit."""
    longest_run = (
        settings.AGENT_ORDER_EVENT_SWEEP_BUDGET_SECONDS
        + settings.AGENT_ORDER_EVENT_CONNECT_TIMEOUT
        + settings.AGENT_ORDER_EVENT_READ_TIMEOUT
    )
    entry = settings.CELERY_BEAT_SCHEDULE["deliver-due-agent-order-events"]

    assert entry["schedule"] == SCHEDULE_PRESETS["every_minute"]
    assert longest_run < timedelta(minutes=1).total_seconds()
    assert longest_run < settings.CELERY_TASK_SOFT_TIME_LIMIT


class _TricklingGateway(socketserver.BaseRequestHandler):
    """Answers a status line, then a header byte at a time: every wait
    for the next byte is short, the whole answer never ends."""

    stop = threading.Event()

    def handle(self) -> None:
        self.request.recv(65536)
        self.request.sendall(b"HTTP/1.1 200 OK\r\n")
        while not self.stop.wait(0.1):
            try:
                self.request.sendall(b"X")
            except OSError:
                return


class CallDeadlineTests(TestCase):
    def setUp(self):
        _TricklingGateway.stop.clear()
        self.server = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0), _TricklingGateway
        )
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(_TricklingGateway.stop.set)
        with override_settings(AGENT_GATEWAY_INTERNAL_URL=""):
            self.order = _order()

    def test_a_trickling_answer_ends_at_connect_plus_read(self):
        host, port = self.server.server_address
        connect, read = 0.5, 1.0
        event = AgentOrderEvent.objects.create(
            order=self.order, event_type=EventType.UPDATED
        )

        with override_settings(
            AGENT_GATEWAY_INTERNAL_URL=f"http://{host}:{port}",
            AGENT_ORDER_EVENT_CONNECT_TIMEOUT=connect,
            AGENT_ORDER_EVENT_READ_TIMEOUT=read,
        ):
            started = time.monotonic()
            outcome = outbox.attempt(event.pk)
            elapsed = time.monotonic() - started

        self.assertEqual(outcome, outbox.Outcome.UNREACHABLE)
        # Each byte comes well within the read timeout; only the deadline
        # ends the call.
        self.assertGreaterEqual(elapsed, connect + read)
        self.assertLess(elapsed, connect + read + 1)
        event.refresh_from_db()
        self.assertEqual(event.status, Status.PENDING)
        self.assertEqual(event.last_error, "TimeoutError")
