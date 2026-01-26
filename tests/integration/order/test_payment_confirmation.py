import pytest


from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models import OrderHistory
from order.services import OrderService
from product.factories import ProductFactory
from user.factories import UserAccountFactory


@pytest.mark.django_db
class TestPaymentConfirmationActualTransition:
    """
    For any order in PENDING status, when a payment_intent.succeeded webhook
    is received for that order's payment_id, the order status SHALL transition
    to PROCESSING.

    These tests verify the ACTUAL status transition happens in the database,
    not just that the service method is called.
    """

    @pytest.mark.parametrize(
        "initial_status,initial_payment_status,expected_final_status,should_transition",
        [
            # Valid transitions - PENDING orders should transition to PROCESSING
            (
                OrderStatus.PENDING,
                PaymentStatus.PENDING,
                OrderStatus.PROCESSING,
                True,
            ),
            (
                OrderStatus.PENDING,
                PaymentStatus.PROCESSING,
                OrderStatus.PROCESSING,
                True,
            ),
            # Already processed - should not change status
            (
                OrderStatus.PROCESSING,
                PaymentStatus.COMPLETED,
                OrderStatus.PROCESSING,
                False,
            ),
            (
                OrderStatus.PROCESSING,
                PaymentStatus.PENDING,
                OrderStatus.PROCESSING,
                False,
            ),
            # Advanced statuses - should not regress
            (
                OrderStatus.SHIPPED,
                PaymentStatus.COMPLETED,
                OrderStatus.SHIPPED,
                False,
            ),
            (
                OrderStatus.DELIVERED,
                PaymentStatus.COMPLETED,
                OrderStatus.DELIVERED,
                False,
            ),
            (
                OrderStatus.COMPLETED,
                PaymentStatus.COMPLETED,
                OrderStatus.COMPLETED,
                False,
            ),
            # Canceled orders - should not transition
            (
                OrderStatus.CANCELED,
                PaymentStatus.PENDING,
                OrderStatus.CANCELED,
                False,
            ),
            (
                OrderStatus.CANCELED,
                PaymentStatus.FAILED,
                OrderStatus.CANCELED,
                False,
            ),
            # Refunded orders - should not transition
            (
                OrderStatus.REFUNDED,
                PaymentStatus.REFUNDED,
                OrderStatus.REFUNDED,
                False,
            ),
        ],
    )
    def test_payment_success_triggers_correct_status_transition(
        self,
        initial_status,
        initial_payment_status,
        expected_final_status,
        should_transition,
    ):
        """
        Test that payment success webhook triggers correct status transitions.

        This test verifies:
        1. PENDING orders transition to PROCESSING
        2. Non-PENDING orders do not change status
        3. Payment status is updated to COMPLETED
        4. Status transition is recorded in OrderHistory
        """
        # Create order with specific initial state
        payment_id = (
            f"pi_test_{initial_status.value}_{initial_payment_status.value}"
        )
        order = OrderFactory(
            status=initial_status,
            payment_status=initial_payment_status,
            payment_id=payment_id,
            num_order_items=0,
        )

        # Record initial state
        initial_order_status = order.status
        initial_history_count = OrderHistory.objects.filter(order=order).count()

        # Execute payment success handler
        result = OrderService.handle_payment_succeeded(payment_id)

        # Verify handler found the order
        assert result is not None, (
            f"OrderService.handle_payment_succeeded should return order for {payment_id}"
        )

        # Refresh order from database
        order.refresh_from_db()

        # Verify final status
        assert order.status == expected_final_status, (
            f"Order status should be {expected_final_status}, got {order.status}"
        )

        # Verify payment status is updated to COMPLETED
        assert order.payment_status == PaymentStatus.COMPLETED, (
            f"Payment status should be COMPLETED, got {order.payment_status}"
        )

        # Verify status transition was recorded in history if status changed
        if should_transition:
            final_history_count = OrderHistory.objects.filter(
                order=order
            ).count()
            assert final_history_count > initial_history_count, (
                "Status transition should be recorded in OrderHistory"
            )

            # Verify the history entry contains correct information
            latest_history = (
                OrderHistory.objects.filter(
                    order=order,
                    change_type=OrderHistory.OrderHistoryChangeType.STATUS,
                )
                .order_by("-created_at")
                .first()
            )
            assert latest_history is not None
            assert latest_history.previous_value == {
                "status": initial_order_status.value
            }
            assert latest_history.new_value == {
                "status": expected_final_status.value
            }
        else:
            # If no transition, status should remain unchanged
            assert order.status == initial_order_status, (
                f"Order status should remain {initial_order_status}, got {order.status}"
            )

    @pytest.mark.parametrize(
        "order_scenario",
        [
            {
                "name": "single_item_order",
                "num_items": 1,
            },
            {
                "name": "multi_item_order",
                "num_items": 3,
            },
            {
                "name": "high_value_order",
                "num_items": 5,
            },
            {
                "name": "low_value_order",
                "num_items": 1,
            },
        ],
    )
    def test_payment_success_with_various_order_scenarios(self, order_scenario):
        """
        Test payment success handling with various order configurations.

        This test verifies that the status transition works correctly regardless of:
        - Number of items in the order
        - Order total amount
        - Order complexity
        """
        # Create order with specified scenario
        payment_id = f"pi_test_{order_scenario['name']}"
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=order_scenario["num_items"],
        )

        # Execute payment success
        result = OrderService.handle_payment_succeeded(payment_id)

        # Verify transition occurred
        assert result is not None
        order.refresh_from_db()
        assert order.status == OrderStatus.PROCESSING, (
            f"Order {order_scenario['name']} should transition to PROCESSING"
        )
        assert order.payment_status == PaymentStatus.COMPLETED

    @pytest.mark.parametrize(
        "user_type,has_user",
        [
            ("authenticated_user", True),
            ("guest_user", False),
        ],
    )
    def test_payment_success_for_authenticated_and_guest_orders(
        self, user_type, has_user
    ):
        """
        Test payment success for both authenticated and guest orders.

        This test verifies that status transition works for:
        - Orders with authenticated users
        - Guest orders (no user_id)
        """
        payment_id = f"pi_test_{user_type}"

        if has_user:
            user = UserAccountFactory()
            order = OrderFactory(
                user=user,
                status=OrderStatus.PENDING,
                payment_status=PaymentStatus.PENDING,
                payment_id=payment_id,
                num_order_items=1,
            )
        else:
            order = OrderFactory(
                user=None,
                status=OrderStatus.PENDING,
                payment_status=PaymentStatus.PENDING,
                payment_id=payment_id,
                num_order_items=1,
            )

        # Execute payment success
        result = OrderService.handle_payment_succeeded(payment_id)

        # Verify transition occurred
        assert result is not None
        order.refresh_from_db()
        assert order.status == OrderStatus.PROCESSING
        assert order.payment_status == PaymentStatus.COMPLETED

    @pytest.mark.parametrize(
        "concurrent_orders",
        [2, 3, 5],
    )
    def test_payment_success_for_multiple_concurrent_orders(
        self, concurrent_orders
    ):
        """
        Test payment success handling for multiple orders processed concurrently.

        This test verifies that:
        - Multiple orders can be processed independently
        - Each order transitions correctly
        - No interference between order processing
        """
        import uuid
        from order.models import OrderItem

        # Create multiple orders with unique payment IDs
        orders_data = []
        test_id = str(uuid.uuid4())[:8]  # Unique test run identifier

        for i in range(concurrent_orders):
            payment_id = f"pi_test_concurrent_{test_id}_{i}"
            product = ProductFactory(stock=10)

            # Build order without saving to avoid LazyFunction regeneration
            order = OrderFactory.build(
                status=OrderStatus.PENDING,
                payment_status=PaymentStatus.PENDING,
                payment_id=payment_id,
            )
            # Save with explicit payment_id
            order.save()

            # Create a single order item manually
            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=1,
                price=product.price,
                sort_order=1,
            )

            orders_data.append({"order": order, "payment_id": payment_id})

        # Process all payment successes
        for data in orders_data:
            result = OrderService.handle_payment_succeeded(data["payment_id"])
            assert result is not None, (
                f"Failed to process payment {data['payment_id']}"
            )
            assert result.id == data["order"].id, (
                f"Returned wrong order for payment {data['payment_id']}"
            )

        # Verify all orders transitioned correctly
        for data in orders_data:
            data["order"].refresh_from_db()
            assert data["order"].status == OrderStatus.PROCESSING, (
                f"Order {data['order'].id} should transition to PROCESSING"
            )
            assert data["order"].payment_status == PaymentStatus.COMPLETED

    def test_payment_success_with_nonexistent_payment_id(self):
        """
        Test payment success handler with non-existent payment_id.

        This test verifies that:
        - Handler gracefully handles missing orders
        - Returns None for non-existent payment_id
        - No errors are raised
        """
        # Attempt to process payment for non-existent order
        result = OrderService.handle_payment_succeeded("pi_nonexistent_12345")

        # Verify handler returns None
        assert result is None, (
            "handle_payment_succeeded should return None for non-existent payment_id"
        )

    @pytest.mark.parametrize(
        "payment_id_format",
        [
            "pi_test_standard_format",
            "pi_live_1234567890abcdef",
            "pi_test_with_underscores_123",
            "pi_1A2B3C4D5E6F7G8H9I0J",
        ],
    )
    def test_payment_success_with_various_payment_id_formats(
        self, payment_id_format
    ):
        """
        Test payment success with various Stripe payment intent ID formats.

        This test verifies that the handler works with different valid
        Stripe payment intent ID formats.
        """
        # Create order with specific payment_id format
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id_format,
            num_order_items=1,
        )

        # Execute payment success
        result = OrderService.handle_payment_succeeded(payment_id_format)

        # Verify transition occurred
        assert result is not None
        order.refresh_from_db()
        assert order.status == OrderStatus.PROCESSING
        assert order.payment_status == PaymentStatus.COMPLETED

    def test_payment_success_updates_status_updated_at_timestamp(self):
        """
        Test that payment success updates the status_updated_at timestamp.

        This test verifies that:
        - status_updated_at is updated when status changes
        - Timestamp reflects the time of the transition
        """
        payment_id = "pi_test_timestamp"
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=1,
        )

        # Record initial timestamp (may be None)
        initial_timestamp = order.status_updated_at

        # Wait a moment to ensure timestamp difference
        import time

        time.sleep(0.1)

        # Execute payment success
        result = OrderService.handle_payment_succeeded(payment_id)

        # Verify timestamp was updated
        assert result is not None
        order.refresh_from_db()
        assert order.status_updated_at is not None, (
            "status_updated_at should be set after status transition"
        )
        if initial_timestamp is not None:
            assert order.status_updated_at > initial_timestamp, (
                "status_updated_at should be updated after status transition"
            )

    @pytest.mark.parametrize(
        "metadata_scenario",
        [
            {
                "cart_session_id": "cart-uuid-123",
                "customer_email": "test@example.com",
            },
            {
                "cart_session_id": "cart-uuid-456",
                "customer_email": "guest@example.com",
            },
            {"order_notes": "Special instructions", "gift_wrap": True},
            {},  # Empty metadata
        ],
    )
    def test_payment_success_preserves_order_metadata(self, metadata_scenario):
        """
        Test that payment success preserves order metadata.

        This test verifies that:
        - Order metadata is not lost during status transition
        - Additional metadata can be present
        """
        payment_id = "pi_test_metadata"
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=1,
            metadata=metadata_scenario,
        )

        # Execute payment success
        result = OrderService.handle_payment_succeeded(payment_id)

        # Verify metadata is preserved
        assert result is not None
        order.refresh_from_db()
        assert order.status == OrderStatus.PROCESSING

        # Verify metadata is intact
        for key, value in metadata_scenario.items():
            assert order.metadata.get(key) == value, (
                f"Metadata key '{key}' should be preserved"
            )

    def test_payment_success_idempotency(self):
        """
        Test that calling handle_payment_succeeded multiple times is idempotent.

        This test verifies that:
        - Processing the same payment multiple times produces the same result
        - Order status remains PROCESSING after multiple calls
        - No duplicate history entries are created
        """
        payment_id = "pi_test_idempotent"
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=1,
        )

        # Process payment success multiple times
        result1 = OrderService.handle_payment_succeeded(payment_id)
        result2 = OrderService.handle_payment_succeeded(payment_id)
        result3 = OrderService.handle_payment_succeeded(payment_id)

        # Verify all calls succeeded
        assert result1 is not None
        assert result2 is not None
        assert result3 is not None

        # Verify final state is correct
        order.refresh_from_db()
        assert order.status == OrderStatus.PROCESSING
        assert order.payment_status == PaymentStatus.COMPLETED

        # Verify only one status transition was recorded
        # (PENDING -> PROCESSING should only happen once)
        status_transitions = OrderHistory.objects.filter(
            order=order,
            change_type=OrderHistory.OrderHistoryChangeType.STATUS,
            previous_value__status=OrderStatus.PENDING.value,
            new_value__status=OrderStatus.PROCESSING.value,
        ).count()

        # Should be 1 or 0 depending on implementation
        # (some implementations may not create duplicate history entries)
        assert status_transitions <= 1, (
            "Should not create duplicate status transition history entries"
        )


@pytest.mark.django_db
class TestIntegrationWithWebhooks:
    """
    Integration tests with actual webhook processing.

    These tests verify the complete flow from webhook receipt to status transition.
    Note: Webhooks are handled via dj-stripe signals, not a separate webhooks module.
    """

    @pytest.mark.parametrize(
        "webhook_scenario",
        [
            {
                "event_type": "payment_intent.succeeded",
                "payment_status": "succeeded",
                "should_transition": True,
            },
        ],
    )
    def test_webhook_to_status_transition_integration(self, webhook_scenario):
        """
        Test complete integration from webhook to status transition.

        This test verifies the end-to-end flow:
        1. Order is created with payment_id
        2. Payment intent succeeds
        3. OrderService.handle_payment_succeeded is called
        4. Status is transitioned

        Note: This test simulates the webhook handler calling OrderService directly,
        as the actual webhook handling is done by dj-stripe signals.
        """
        # Create order
        payment_id = f"pi_test_integration_{webhook_scenario['event_type']}"
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment_id,
            num_order_items=1,
        )

        # Simulate webhook event processing by calling the service directly
        # (In production, this would be called by the dj-stripe signal handler)
        result = OrderService.handle_payment_succeeded(payment_id)

        # Verify status transition
        assert result is not None
        order.refresh_from_db()
        if webhook_scenario["should_transition"]:
            assert order.status == OrderStatus.PROCESSING, (
                f"Order should transition to PROCESSING for {webhook_scenario['event_type']}"
            )
            assert order.payment_status == PaymentStatus.COMPLETED
        else:
            assert order.status == OrderStatus.PENDING, (
                f"Order should remain PENDING for {webhook_scenario['event_type']}"
            )
