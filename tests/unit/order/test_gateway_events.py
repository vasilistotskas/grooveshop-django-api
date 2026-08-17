"""Tests for the agent-gateway order-event push.

Two layers under test:

- ``push_order_event_to_gateway`` (order/tasks.py): POSTs the event to
  the gateway's cluster-internal endpoint, no-ops when the gateway URL
  is unset, retries on transport errors / gateway 5xx, drops on 4xx.
- ``handle_order_post_save`` wiring: exactly one enqueue per save that
  changed ``status``, ``payment_status`` or set tracking info; nothing
  for unrelated saves, creations, or when the gateway URL is unset.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import requests
from celery.exceptions import Retry as CeleryRetry
from django.db import connection
from django.test import TestCase, override_settings

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.tasks import push_order_event_to_gateway

GATEWAY_URL = "http://agent-gateway-service"
GATEWAY_SECRET = "test-internal-secret"


def _ok_response(status_code: int = 204) -> Mock:
    response = Mock()
    response.status_code = status_code
    return response


class PushOrderEventTaskTestCase(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            tracking_number="",
        )

    @override_settings(AGENT_GATEWAY_INTERNAL_URL="")
    @patch("order.tasks.requests.post")
    def test_noop_when_gateway_url_unset(self, mock_post) -> None:
        push_order_event_to_gateway(self.order.id)
        mock_post.assert_not_called()

    @override_settings(
        AGENT_GATEWAY_INTERNAL_URL=GATEWAY_URL,
        AGENT_GATEWAY_INTERNAL_SECRET=GATEWAY_SECRET,
    )
    @patch("order.tasks.requests.post")
    def test_posts_event_with_body_and_secret(self, mock_post) -> None:
        mock_post.return_value = _ok_response()
        push_order_event_to_gateway(self.order.id)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], f"{GATEWAY_URL}/internal/events/order-status")
        self.assertEqual(
            kwargs["headers"], {"X-Internal-Token": GATEWAY_SECRET}
        )
        body = kwargs["json"]
        self.assertEqual(body["orderUuid"], str(self.order.uuid))
        self.assertEqual(body["status"], OrderStatus.PENDING.value)
        self.assertEqual(body["paymentStatus"], PaymentStatus.PENDING.value)
        self.assertIn("schemaName", body)
        # tracking_number is unset on a fresh order → empty string, not
        # None (the gateway decodes plain strings).
        self.assertEqual(body["trackingNumber"], "")

    @override_settings(AGENT_GATEWAY_INTERNAL_URL=GATEWAY_URL)
    @patch("order.tasks.requests.post")
    def test_vanished_order_is_dropped(self, mock_post) -> None:
        push_order_event_to_gateway(self.order.id + 999_999)
        mock_post.assert_not_called()

    @override_settings(AGENT_GATEWAY_INTERNAL_URL=GATEWAY_URL)
    @patch("order.tasks.requests.post")
    def test_gateway_5xx_requests_retry(self, mock_post) -> None:
        response = _ok_response(503)
        response.raise_for_status.side_effect = requests.HTTPError(
            "503 Service Unavailable"
        )
        mock_post.return_value = response
        # Under EAGER the autoretry surfaces as celery.exceptions.Retry
        # (eager mode does not re-execute the task) — proving the 5xx
        # took the retry path instead of being swallowed.
        with self.assertRaises(CeleryRetry):
            push_order_event_to_gateway.delay(self.order.id)
        mock_post.assert_called_once()

    @override_settings(AGENT_GATEWAY_INTERNAL_URL=GATEWAY_URL)
    @patch("order.tasks.requests.post")
    def test_gateway_4xx_is_logged_not_retried(self, mock_post) -> None:
        response = _ok_response(403)
        response.text = "forbidden"
        mock_post.return_value = response
        push_order_event_to_gateway(self.order.id)
        mock_post.assert_called_once()
        response.raise_for_status.assert_not_called()


@override_settings(AGENT_GATEWAY_INTERNAL_URL=GATEWAY_URL)
class GatewayEventSignalWiringTestCase(TestCase):
    def _fresh_order(self) -> Order:
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        order.tracking_number = ""
        order.shipping_carrier = ""
        order.save()
        # Re-read so the _original_* snapshots match the DB row.
        return Order.objects.get(pk=order.pk)

    @patch("order.signals.handlers.push_order_event_to_gateway")
    def test_enqueued_on_status_change(self, mock_task) -> None:
        order = self._fresh_order()
        with self.captureOnCommitCallbacks(execute=True):
            order.status = OrderStatus.PROCESSING
            order.save()
        mock_task.apply_async.assert_called_once_with(
            args=[order.id],
            headers={"_schema_name": connection.schema_name},
        )

    @patch("order.signals.handlers.push_order_event_to_gateway")
    def test_enqueued_on_payment_status_change_alone(self, mock_task) -> None:
        order = self._fresh_order()
        with self.captureOnCommitCallbacks(execute=True):
            order.payment_status = PaymentStatus.COMPLETED
            order.save()
        mock_task.apply_async.assert_called_once_with(
            args=[order.id],
            headers={"_schema_name": connection.schema_name},
        )

    @patch("order.signals.handlers.push_order_event_to_gateway")
    def test_enqueued_on_tracking_set(self, mock_task) -> None:
        order = self._fresh_order()
        with self.captureOnCommitCallbacks(execute=True):
            order.tracking_number = "ABC123"
            order.shipping_carrier = "ELTA"
            order.save()
        mock_task.apply_async.assert_called_once_with(
            args=[order.id],
            headers={"_schema_name": connection.schema_name},
        )

    @patch("order.signals.handlers.push_order_event_to_gateway")
    def test_single_enqueue_when_status_and_payment_change_together(
        self, mock_task
    ) -> None:
        order = self._fresh_order()
        with self.captureOnCommitCallbacks(execute=True):
            order.status = OrderStatus.PROCESSING
            order.payment_status = PaymentStatus.COMPLETED
            order.save()
        mock_task.apply_async.assert_called_once_with(
            args=[order.id],
            headers={"_schema_name": connection.schema_name},
        )

    @patch("order.signals.handlers.push_order_event_to_gateway")
    def test_not_enqueued_on_unrelated_save(self, mock_task) -> None:
        order = self._fresh_order()
        with self.captureOnCommitCallbacks(execute=True):
            order.customer_notes = "please ring the bell"
            order.save()
        mock_task.apply_async.assert_not_called()

    @patch("order.signals.handlers.push_order_event_to_gateway")
    def test_not_enqueued_on_creation(self, mock_task) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            OrderFactory(
                status=OrderStatus.PENDING,
                payment_status=PaymentStatus.PENDING,
            )
        mock_task.apply_async.assert_not_called()

    @override_settings(AGENT_GATEWAY_INTERNAL_URL="")
    @patch("order.signals.handlers.push_order_event_to_gateway")
    def test_not_enqueued_when_gateway_url_unset(self, mock_task) -> None:
        order = self._fresh_order()
        with self.captureOnCommitCallbacks(execute=True):
            order.status = OrderStatus.PROCESSING
            order.save()
        mock_task.apply_async.assert_not_called()
