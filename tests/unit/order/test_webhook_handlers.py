import pytest
from unittest.mock import Mock, patch
from djstripe.models import Event

from order.enum.status import OrderStatus, PaymentStatus
from order.models import OrderHistory
from order.signals.handlers import (
    handle_stripe_payment_succeeded,
    handle_stripe_payment_failed,
)
from order.factories import OrderFactory


@pytest.fixture
def mock_djstripe_event():
    """Create a mock dj-stripe Event object."""
    event = Mock(spec=Event)
    event.id = "evt_test_123456"
    event.type = "payment_intent.succeeded"
    event.data = {
        "object": {
            "id": "pi_test_123456",
            "amount": 5000,
            "currency": "usd",
            "status": "succeeded",
        }
    }
    return event


@pytest.fixture
def mock_failed_event():
    """Create a mock dj-stripe Event for payment failure."""
    event = Mock(spec=Event)
    event.id = "evt_test_failed_123"
    event.type = "payment_intent.payment_failed"
    event.data = {
        "object": {
            "id": "pi_test_failed_456",
            "amount": 5000,
            "currency": "usd",
            "status": "failed",
            "last_payment_error": {"message": "Card declined"},
        }
    }
    return event


@pytest.mark.django_db
class TestHandleStripePaymentSucceeded:
    """
    Test suite for handle_stripe_payment_succeeded handler.
    """

    def test_successful_payment_calls_order_service(self, mock_djstripe_event):
        """
        Test that successful payment webhook calls OrderService.handle_payment_succeeded.
        """
        # Setup
        payment_intent_id = "pi_test_123456"
        order = OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )

        # Execute
        with patch(
            "order.signals.handlers.OrderService.handle_payment_succeeded"
        ) as mock_service:
            mock_service.return_value = order
            handle_stripe_payment_succeeded(
                sender=None, event=mock_djstripe_event
            )

        # Verify
        mock_service.assert_called_once_with(payment_intent_id)

    def test_successful_payment_logs_order_history(self, mock_djstripe_event):
        """
        Test that successful payment creates OrderHistory entry.
        """
        # Setup
        payment_intent_id = "pi_test_123456"
        order = OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )

        initial_history_count = OrderHistory.objects.filter(order=order).count()

        # Execute
        with patch(
            "order.signals.handlers.OrderService.handle_payment_succeeded"
        ) as mock_service:
            mock_service.return_value = order
            handle_stripe_payment_succeeded(
                sender=None, event=mock_djstripe_event
            )

        # Verify OrderHistory was created
        history_entries = OrderHistory.objects.filter(order=order).order_by(
            "-created_at"
        )
        assert history_entries.count() == initial_history_count + 1

        latest_entry = history_entries.first()
        assert "payment_status" in str(latest_entry.previous_value)
        assert "completed" in str(latest_entry.new_value)

    def test_idempotency_prevents_duplicate_processing(
        self, mock_djstripe_event
    ):
        """
        Test that duplicate webhook events are not processed twice.
        """
        # Setup
        payment_intent_id = "pi_test_123456"
        event_id = mock_djstripe_event.id

        OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            metadata={f"webhook_processed_{event_id}": True},
        )

        # Execute
        with patch(
            "order.signals.handlers.OrderService.handle_payment_succeeded"
        ) as mock_service:
            handle_stripe_payment_succeeded(
                sender=None, event=mock_djstripe_event
            )

        # Verify OrderService was NOT called (idempotency check)
        mock_service.assert_not_called()

    def test_idempotency_marks_webhook_as_processed(self, mock_djstripe_event):
        """
        Test that webhook event is marked as processed in order metadata.
        """
        # Setup
        payment_intent_id = "pi_test_123456"
        event_id = mock_djstripe_event.id

        order = OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            metadata={},
        )

        # Execute
        with patch(
            "order.signals.handlers.OrderService.handle_payment_succeeded"
        ) as mock_service:
            mock_service.return_value = order
            handle_stripe_payment_succeeded(
                sender=None, event=mock_djstripe_event
            )

        # Verify webhook marked as processed
        order.refresh_from_db()
        assert order.metadata.get(f"webhook_processed_{event_id}") is True

    def test_handles_order_not_found_gracefully(self, mock_djstripe_event):
        """
        Test that handler gracefully handles case when order is not found.
        """
        # Setup - no order exists for this payment_intent_id
        payment_intent_id = "pi_nonexistent_123"
        mock_djstripe_event.data["object"]["id"] = payment_intent_id

        # Execute - should not raise exception
        with patch(
            "order.signals.handlers.OrderService.handle_payment_succeeded"
        ) as mock_service:
            mock_service.return_value = None
            handle_stripe_payment_succeeded(
                sender=None, event=mock_djstripe_event
            )

        # Verify service was called but no error raised
        mock_service.assert_called_once_with(payment_intent_id)

    def test_handles_service_exception_gracefully(self, mock_djstripe_event):
        """
        Test that handler catches and logs exceptions from OrderService.
        """
        # Setup
        payment_intent_id = "pi_test_123456"

        # Execute
        with patch(
            "order.signals.handlers.OrderService.handle_payment_succeeded"
        ) as mock_service:
            mock_service.side_effect = Exception("Database error")

            # Should not raise exception (caught and logged)
            handle_stripe_payment_succeeded(
                sender=None, event=mock_djstripe_event
            )

        # Verify service was called
        mock_service.assert_called_once_with(payment_intent_id)

    def test_extracts_payment_intent_id_correctly(self, mock_djstripe_event):
        """
        Test that payment_intent_id is correctly extracted from event data.
        """
        # Setup
        payment_intent_id = "pi_custom_789"
        mock_djstripe_event.data["object"]["id"] = payment_intent_id

        order = OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
        )

        # Execute
        with patch(
            "order.signals.handlers.OrderService.handle_payment_succeeded"
        ) as mock_service:
            mock_service.return_value = order
            handle_stripe_payment_succeeded(
                sender=None, event=mock_djstripe_event
            )

        # Verify correct payment_intent_id was used
        mock_service.assert_called_once_with(payment_intent_id)


@pytest.mark.django_db
class TestHandleStripePaymentFailed:
    """
    Test suite for handle_stripe_payment_failed handler.
    """

    def test_failed_payment_calls_order_service(self, mock_failed_event):
        """
        Test that failed payment webhook calls OrderService.handle_payment_failed.
        """
        # Setup
        payment_intent_id = "pi_test_failed_456"
        order = OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )

        # Execute
        with patch(
            "order.signals.handlers.OrderService.handle_payment_failed"
        ) as mock_service:
            mock_service.return_value = order
            handle_stripe_payment_failed(sender=None, event=mock_failed_event)

        # Verify
        mock_service.assert_called_once_with(payment_intent_id)

    def test_failed_payment_logs_order_history(self, mock_failed_event):
        """
        Test that failed payment creates OrderHistory entry.
        """
        # Setup
        payment_intent_id = "pi_test_failed_456"
        order = OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )

        initial_history_count = OrderHistory.objects.filter(order=order).count()

        # Execute
        with patch(
            "order.signals.handlers.OrderService.handle_payment_failed"
        ) as mock_service:
            mock_service.return_value = order
            handle_stripe_payment_failed(sender=None, event=mock_failed_event)

        # Verify OrderHistory was created
        history_entries = OrderHistory.objects.filter(order=order).order_by(
            "-created_at"
        )
        assert history_entries.count() == initial_history_count + 1

        latest_entry = history_entries.first()
        assert "payment_status" in str(latest_entry.previous_value)
        assert "failed" in str(latest_entry.new_value)

    def test_handles_order_not_found_gracefully(self, mock_failed_event):
        """
        Test that handler gracefully handles case when order is not found.
        """
        # Setup - no order exists for this payment_intent_id
        payment_intent_id = "pi_nonexistent_failed"
        mock_failed_event.data["object"]["id"] = payment_intent_id

        # Execute - should not raise exception
        with patch(
            "order.signals.handlers.OrderService.handle_payment_failed"
        ) as mock_service:
            mock_service.return_value = None
            handle_stripe_payment_failed(sender=None, event=mock_failed_event)

        # Verify service was called but no error raised
        mock_service.assert_called_once_with(payment_intent_id)

    def test_handles_service_exception_gracefully(self, mock_failed_event):
        """
        Test that handler catches and logs exceptions from OrderService.
        """
        # Setup
        payment_intent_id = "pi_test_failed_456"

        # Execute
        with patch(
            "order.signals.handlers.OrderService.handle_payment_failed"
        ) as mock_service:
            mock_service.side_effect = Exception("Database error")

            # Should not raise exception (caught and logged)
            handle_stripe_payment_failed(sender=None, event=mock_failed_event)

        # Verify service was called
        mock_service.assert_called_once_with(payment_intent_id)

    def test_extracts_payment_intent_id_correctly(self, mock_failed_event):
        """
        Test that payment_intent_id is correctly extracted from event data.
        """
        # Setup
        payment_intent_id = "pi_custom_failed_999"
        mock_failed_event.data["object"]["id"] = payment_intent_id

        order = OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
        )

        # Execute
        with patch(
            "order.signals.handlers.OrderService.handle_payment_failed"
        ) as mock_service:
            mock_service.return_value = order
            handle_stripe_payment_failed(sender=None, event=mock_failed_event)

        # Verify correct payment_intent_id was used
        mock_service.assert_called_once_with(payment_intent_id)


@pytest.mark.django_db
class TestWebhookHandlerErrorHandling:
    """
    Test error handling and edge cases for webhook handlers.
    """

    def test_handles_missing_event_data(self):
        """
        Test that handler handles missing event data gracefully.
        """
        # Setup - event with missing data
        event = Mock(spec=Event)
        event.id = "evt_malformed"
        event.data = {}  # Missing 'object' key

        # Execute - should not raise exception
        with patch("order.signals.handlers.logger") as mock_logger:
            handle_stripe_payment_succeeded(sender=None, event=event)

            # Verify error was logged
            assert mock_logger.error.called

    def test_handles_malformed_payment_intent_id(self, mock_djstripe_event):
        """
        Test that handler handles malformed payment_intent_id.
        """
        # Setup - event with None payment_intent_id
        mock_djstripe_event.data["object"]["id"] = None

        # Execute - should not raise exception
        with patch(
            "order.signals.handlers.OrderService.handle_payment_succeeded"
        ) as mock_service:
            handle_stripe_payment_succeeded(
                sender=None, event=mock_djstripe_event
            )

            # Verify service was called with None
            mock_service.assert_called_once_with(None)

    def test_handles_concurrent_webhook_processing(self, mock_djstripe_event):
        """
        Test that concurrent webhook processing is handled via idempotency.
        """
        # Setup
        payment_intent_id = "pi_concurrent_test"
        mock_djstripe_event.data["object"]["id"] = payment_intent_id

        order = OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
            metadata={},
        )

        # Simulate first webhook processing
        with patch(
            "order.signals.handlers.OrderService.handle_payment_succeeded"
        ) as mock_service:
            mock_service.return_value = order
            handle_stripe_payment_succeeded(
                sender=None, event=mock_djstripe_event
            )

        # Refresh order to get updated metadata
        order.refresh_from_db()

        # Simulate second webhook processing (duplicate)
        with patch(
            "order.signals.handlers.OrderService.handle_payment_succeeded"
        ) as mock_service:
            handle_stripe_payment_succeeded(
                sender=None, event=mock_djstripe_event
            )

            # Verify service was NOT called second time
            mock_service.assert_not_called()


@pytest.mark.django_db
class TestWebhookHandlerIntegration:
    """
    Integration tests for webhook handlers with real OrderService calls.

    These tests verify the complete flow without mocking OrderService.
    """

    def test_payment_succeeded_updates_order_status(self, mock_djstripe_event):
        """
        Test that successful payment updates order status to PROCESSING.
        """
        # Setup
        payment_intent_id = "pi_integration_test"
        mock_djstripe_event.data["object"]["id"] = payment_intent_id

        order = OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            metadata={},
        )

        # Execute
        handle_stripe_payment_succeeded(sender=None, event=mock_djstripe_event)

        # Verify
        order.refresh_from_db()
        assert order.status == OrderStatus.PROCESSING.value
        assert order.payment_status == PaymentStatus.COMPLETED.value

    def test_payment_failed_updates_payment_status(self, mock_failed_event):
        """
        Test that failed payment updates payment status to FAILED.
        """
        # Setup
        payment_intent_id = "pi_integration_failed"
        mock_failed_event.data["object"]["id"] = payment_intent_id

        order = OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )

        # Execute
        handle_stripe_payment_failed(sender=None, event=mock_failed_event)

        # Verify
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED.value
        # Status should remain PENDING (not changed on failure)
        assert order.status == OrderStatus.PENDING.value

    def test_multiple_events_for_same_order(self, mock_djstripe_event):
        """
        Test that multiple different events for same order are processed correctly.
        """
        # Setup
        payment_intent_id = "pi_multi_event_test"
        order = OrderFactory(
            payment_id=payment_intent_id,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            metadata={},
        )

        # First event
        event1 = Mock(spec=Event)
        event1.id = "evt_first"
        event1.data = {"object": {"id": payment_intent_id}}

        # Second event (different event_id)
        event2 = Mock(spec=Event)
        event2.id = "evt_second"
        event2.data = {"object": {"id": payment_intent_id}}

        # Execute both events
        handle_stripe_payment_succeeded(sender=None, event=event1)
        handle_stripe_payment_succeeded(sender=None, event=event2)

        # Verify both events were processed (different event IDs)
        order.refresh_from_db()
        assert order.metadata.get("webhook_processed_evt_first") is True
        assert order.metadata.get("webhook_processed_evt_second") is True
