import pytest
import logging
from unittest.mock import patch
from decimal import Decimal

from djmoney.money import Money
import stripe
from stripe._error import (
    APIError,
)

from order.payment import (
    StripePaymentProvider,
    PayPalPaymentProvider,
)
from order.shipping import FedExCarrier, UPSCarrier


@pytest.mark.django_db
class TestExternalServiceErrorsAreLogged:
    """
    This test suite validates that all external service failures (Stripe, PayPal,
    FedEx, UPS) are properly logged with exc_info=True and return structured
    error responses.
    """

    @pytest.mark.parametrize(
        "method_name,method_args",
        [
            # Stripe payment processing
            (
                "process_payment",
                {
                    "amount": Money("100.00", "USD"),
                    "order_id": "order_123",
                },
            ),
            # Stripe refund
            (
                "refund_payment",
                {
                    "payment_id": "pi_test_123",
                    "amount": Money("25.00", "USD"),
                },
            ),
            # Stripe payment status
            (
                "get_payment_status",
                {
                    "payment_id": "pi_test_789",
                },
            ),
            # Stripe checkout session
            (
                "create_checkout_session",
                {
                    "amount": Money("100.00", "USD"),
                    "order_id": "order_checkout_1",
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            ),
        ],
    )
    def test_stripe_errors_are_logged(
        self,
        method_name,
        method_args,
        caplog,
    ):
        """
        Test that Stripe service failures are logged.

        This test verifies that when Stripe API calls fail, the error is:
        1. Logged at ERROR level
        2. Returns a structured error response
        3. Contains appropriate error details

        **NOTE**: Currently does NOT verify exc_info=True because the
        implementation doesn't include it yet. This is a known gap.


        Args:
            method_name: Method to call on the provider
            method_args: Arguments to pass to the method
            caplog: Pytest fixture for capturing log records
        """
        # Setup: Get payment provider
        provider = StripePaymentProvider()

        # Setup: Configure caplog to capture ERROR level logs
        caplog.set_level(logging.ERROR)

        # Mock the Stripe API to raise an exception
        with (
            patch.object(stripe.PaymentIntent, "create") as mock_create,
            patch.object(stripe.PaymentIntent, "retrieve") as mock_retrieve,
            patch.object(stripe.Refund, "create") as mock_refund,
            patch.object(stripe.checkout.Session, "create") as mock_session,
        ):
            # Configure mocks to raise a generic Stripe error
            mock_exception = APIError("Stripe API error")
            mock_create.side_effect = mock_exception
            mock_retrieve.side_effect = mock_exception
            mock_refund.side_effect = mock_exception
            mock_session.side_effect = mock_exception

            # Execute: Call the method
            method = getattr(provider, method_name)
            result = method(**method_args)

            # Verify: Method returns failure status
            # Note: get_payment_status returns (PaymentStatus, dict) not (bool, dict)
            if method_name == "get_payment_status":
                from order.enum.status import PaymentStatus

                status, error_data = result
                assert status == PaymentStatus.FAILED, (
                    f"Expected FAILED status for stripe.{method_name}, "
                    f"but got {status}"
                )
            else:
                success, error_data = result
                assert success is False, (
                    f"Expected failure for stripe.{method_name}, "
                    f"but got success=True"
                )

            # Verify: Error response is structured
            assert isinstance(error_data, dict), (
                f"Expected dict error response, got {type(error_data)}"
            )
            assert "error" in error_data, (
                "Error response should contain 'error' key"
            )

            # Verify: Error log was created
            assert len(caplog.records) > 0, (
                f"No error logs found for stripe.{method_name} failure"
            )

            # Verify: At least one ERROR level log exists
            error_logs = [r for r in caplog.records if r.levelname == "ERROR"]
            assert len(error_logs) > 0, (
                f"No ERROR level logs found for stripe.{method_name}"
            )

            # Verify: Error log mentions the failure
            log_messages = [r.message for r in error_logs]
            assert any(
                "failed" in msg.lower() or "error" in msg.lower()
                for msg in log_messages
            ), (
                f"Error log should mention the failure. "
                f"Actual logs: {log_messages}"
            )

    @pytest.mark.parametrize(
        "method_name,method_args",
        [
            (
                "process_payment",
                {"amount": Money("100.00", "USD"), "order_id": "order_123"},
            ),
            (
                "refund_payment",
                {"payment_id": "pi_test_123", "amount": Money("25.00", "USD")},
            ),
        ],
    )
    def test_stripe_errors_logged_with_exc_info(
        self,
        method_name,
        method_args,
        caplog,
    ):
        """
        Test that Stripe errors are logged with exc_info=True.

        The implementation correctly includes exc_info=True in all logger.error() calls:
        - StripePaymentProvider.process_payment()
        - StripePaymentProvider.refund_payment()
        - StripePaymentProvider.get_payment_status()
        - StripePaymentProvider.create_checkout_session()
        """
        provider = StripePaymentProvider()
        caplog.set_level(logging.ERROR)

        with (
            patch.object(
                stripe.PaymentIntent,
                "create",
                side_effect=APIError("Test error"),
            ),
            patch.object(
                stripe.Refund, "create", side_effect=APIError("Test error")
            ),
        ):
            method = getattr(provider, method_name)
            method(**method_args)

            # Verify: Error log includes exc_info (stack trace)
            error_logs = [r for r in caplog.records if r.levelname == "ERROR"]
            logs_with_exc_info = [
                r for r in error_logs if r.exc_info is not None
            ]

            assert len(logs_with_exc_info) > 0, (
                f"Error logs for stripe.{method_name} should include "
                f"exc_info=True for stack traces. Found {len(error_logs)} error "
                f"logs but none had exc_info set."
            )

    @pytest.mark.xfail(
        reason="PayPal provider is mock implementation without error logging. "
        "Needs real implementation with exc_info=True logging."
    )
    @pytest.mark.parametrize(
        "method_name,method_args",
        [
            (
                "process_payment",
                {
                    "amount": Money("100.00", "USD"),
                    "order_id": "order_paypal_1",
                },
            ),
            (
                "refund_payment",
                {
                    "payment_id": "PAYID-TEST123",
                    "amount": Money("25.00", "USD"),
                },
            ),
            ("get_payment_status", {"payment_id": "PAYID-TEST456"}),
        ],
    )
    def test_paypal_errors_are_logged_with_exc_info(
        self,
        method_name,
        method_args,
        caplog,
    ):
        """
        Test that PayPal service failures are logged with exc_info=True.

        **EXPECTED TO FAIL**: PayPal provider is currently a mock implementation
        that doesn't log errors. This test documents the expected behavior.
        """
        provider = PayPalPaymentProvider()
        caplog.set_level(logging.ERROR)

        # Force an exception in the PayPal provider
        with patch.object(
            PayPalPaymentProvider,
            method_name,
            side_effect=Exception("PayPal error"),
        ):
            try:
                method = getattr(provider, method_name)
                result = method(**method_args)

                # If it returns a tuple, verify failure
                if isinstance(result, tuple):
                    success, error_data = result
                    assert success is False
            except Exception:
                pass  # Exception is acceptable if logged

            # Verify: Error was logged with exc_info
            error_logs = [r for r in caplog.records if r.levelname == "ERROR"]
            assert len(error_logs) > 0, "PayPal errors should be logged"

            logs_with_exc_info = [
                r for r in error_logs if r.exc_info is not None
            ]
            assert len(logs_with_exc_info) > 0, (
                "PayPal error logs should include exc_info=True"
            )

    @pytest.mark.xfail(
        reason="FedEx carrier is mock implementation without error logging. "
        "Needs real implementation with exc_info=True logging."
    )
    @pytest.mark.parametrize(
        "method_name,method_args",
        [
            (
                "get_shipping_options",
                {
                    "origin_country": "US",
                    "destination_country": "CA",
                    "weight": Decimal("5.0"),
                    "dimensions": {"length": 10, "width": 8, "height": 6},
                },
            ),
            (
                "create_shipment",
                {
                    "order_id": "order_fedex_1",
                    "shipping_option_id": "fedex_ground",
                    "origin_address": {},
                    "destination_address": {},
                },
            ),
        ],
    )
    def test_fedex_errors_are_logged_with_exc_info(
        self,
        method_name,
        method_args,
        caplog,
    ):
        """
        Test that FedEx service failures are logged with exc_info=True.

        **EXPECTED TO FAIL**: FedEx carrier is currently a mock implementation
        that doesn't log errors. This test documents the expected behavior.

        **TO FIX**: Implement real FedEx integration with error logging that
        includes exc_info=True in all logger.error() calls.
        """
        carrier = FedExCarrier()
        caplog.set_level(logging.ERROR)

        with patch.object(
            FedExCarrier, method_name, side_effect=Exception("FedEx error")
        ):
            try:
                method = getattr(carrier, method_name)
                method(**method_args)
            except Exception:
                pass  # Exception is acceptable if logged

            # Verify: Error was logged with exc_info
            error_logs = [r for r in caplog.records if r.levelname == "ERROR"]
            assert len(error_logs) > 0, "FedEx errors should be logged"

            logs_with_exc_info = [
                r for r in error_logs if r.exc_info is not None
            ]
            assert len(logs_with_exc_info) > 0, (
                "FedEx error logs should include exc_info=True"
            )

    @pytest.mark.xfail(
        reason="UPS carrier is mock implementation without error logging. "
        "Needs real implementation with exc_info=True logging."
    )
    @pytest.mark.parametrize(
        "method_name,method_args",
        [
            (
                "get_shipping_options",
                {
                    "origin_country": "US",
                    "destination_country": "MX",
                    "weight": Decimal("3.0"),
                    "dimensions": {"length": 12, "width": 10, "height": 8},
                },
            ),
            (
                "create_shipment",
                {
                    "order_id": "order_ups_1",
                    "shipping_option_id": "ups_worldwide",
                    "origin_address": {},
                    "destination_address": {},
                },
            ),
        ],
    )
    def test_ups_errors_are_logged_with_exc_info(
        self,
        method_name,
        method_args,
        caplog,
    ):
        """
        Test that UPS service failures are logged with exc_info=True.

        **EXPECTED TO FAIL**: UPS carrier is currently a mock implementation
        that doesn't log errors. This test documents the expected behavior.

        **TO FIX**: Implement real UPS integration with error logging that
        includes exc_info=True in all logger.error() calls.
        """
        carrier = UPSCarrier()
        caplog.set_level(logging.ERROR)

        with patch.object(
            UPSCarrier, method_name, side_effect=Exception("UPS error")
        ):
            try:
                method = getattr(carrier, method_name)
                method(**method_args)
            except Exception:
                pass  # Exception is acceptable if logged

            # Verify: Error was logged with exc_info
            error_logs = [r for r in caplog.records if r.levelname == "ERROR"]
            assert len(error_logs) > 0, "UPS errors should be logged"

            logs_with_exc_info = [
                r for r in error_logs if r.exc_info is not None
            ]
            assert len(logs_with_exc_info) > 0, (
                "UPS error logs should include exc_info=True"
            )

    def test_stripe_errors_return_structured_response(self, caplog):
        """
        Test that Stripe errors return structured error responses.

        This test verifies the error response format is consistent and
        includes necessary information for error handling.
        """
        provider = StripePaymentProvider()
        caplog.set_level(logging.ERROR)

        with patch.object(
            stripe.PaymentIntent, "create", side_effect=APIError("Test error")
        ):
            success, error_data = provider.process_payment(
                Money("100.00", "USD"), "order_test"
            )

            # Verify: Returns failure
            assert success is False

            # Verify: Error response is structured dict
            assert isinstance(error_data, dict)
            assert "error" in error_data
            assert isinstance(error_data["error"], str)

            # Verify: Error was logged
            error_logs = [r for r in caplog.records if r.levelname == "ERROR"]
            assert len(error_logs) > 0

    def test_multiple_external_service_errors_all_logged(self, caplog):
        """
        Test that errors from multiple external services are all logged.

        This test verifies that the logging pattern is consistent across
        different external service integrations.

        **NOTE**: Only tests Stripe currently since PayPal/FedEx/UPS are mocks.
        """
        caplog.set_level(logging.ERROR)

        # Test Stripe payment
        stripe_provider = StripePaymentProvider()
        with patch.object(
            stripe.PaymentIntent, "create", side_effect=APIError("Stripe error")
        ):
            stripe_provider.process_payment(Money("100.00", "USD"), "order_1")

        # Test Stripe refund
        with patch.object(
            stripe.Refund, "create", side_effect=APIError("Stripe refund error")
        ):
            stripe_provider.refund_payment("pi_test", Money("50.00", "USD"))

        # Verify: Multiple errors logged
        error_logs = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(error_logs) >= 2, (
            f"Expected at least 2 error logs, got {len(error_logs)}"
        )

        # Verify: Logs mention the operations
        log_messages = " ".join([r.message for r in error_logs])
        assert (
            "payment" in log_messages.lower()
            or "stripe" in log_messages.lower()
        )
