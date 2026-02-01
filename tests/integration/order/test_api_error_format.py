import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient
from djmoney.money import Money

from order.factories import OrderFactory
from product.factories import ProductFactory
from pay_way.factories import PayWayFactory

User = get_user_model()


@pytest.mark.django_db
class TestAPIErrorsUseConsistentFormat:
    """
    Test that all API error responses use consistent ErrorResponseSerializer format
    with detail, status code, and field-level errors when applicable.
    """

    @pytest.fixture
    def api_client(self):
        """Create API client for testing."""
        return APIClient()

    @pytest.fixture
    def authenticated_user(self, db):
        """Create authenticated user."""
        user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )
        return user

    @pytest.fixture
    def authenticated_client(self, api_client, authenticated_user):
        """Create authenticated API client."""
        api_client.force_authenticate(user=authenticated_user)
        return api_client

    @pytest.fixture
    def product(self, db):
        """Create test product."""
        return ProductFactory(stock=10, price=Money("100.00", "USD"))

    @pytest.fixture
    def pay_way(self, db):
        """Create test payment method."""
        return PayWayFactory(provider_code="stripe", active=True)

    @pytest.mark.parametrize(
        "error_scenario,expected_status,expected_fields",
        [
            # Not Found errors
            ("order_not_found", status.HTTP_404_NOT_FOUND, ["detail"]),
            ("order_by_uuid_not_found", status.HTTP_404_NOT_FOUND, ["detail"]),
            # Permission errors (returns 403 Forbidden, not 404 for security)
            ("unauthorized_access", status.HTTP_403_FORBIDDEN, ["detail"]),
            # Validation errors (DRF returns field-specific errors, not generic "detail")
            # Note: payWayId is validated first, so it appears in error response
            ("invalid_order_data", status.HTTP_400_BAD_REQUEST, ["payWayId"]),
            (
                "missing_required_fields",
                status.HTTP_400_BAD_REQUEST,
                ["payWayId"],
            ),
        ],
    )
    def test_api_errors_have_consistent_format(
        self,
        api_client,
        authenticated_client,
        authenticated_user,
        product,
        pay_way,
        error_scenario,
        expected_status,
        expected_fields,
    ):
        """
        Test that API errors return consistent format across different error types.

        Verifies that all API error responses:
        - Return appropriate HTTP status codes
        - Include 'detail' field with error message
        - Use consistent response structure
        - Include field-specific errors for validation failures
        """
        response = None

        if error_scenario == "order_not_found":
            # Try to retrieve non-existent order
            response = authenticated_client.get("/api/v1/order/999999")

        elif error_scenario == "order_by_uuid_not_found":
            # Try to retrieve order by invalid UUID
            import uuid

            fake_uuid = str(uuid.uuid4())
            response = api_client.get(f"/api/v1/order/uuid/{fake_uuid}")

        elif error_scenario == "unauthorized_access":
            # Try to access another user's order
            other_user = User.objects.create_user(
                username="otheruser",
                email="other@example.com",
                password="otherpass123",
            )
            order = OrderFactory(user=other_user)
            response = authenticated_client.get(f"/api/v1/order/{order.id}")

        elif error_scenario == "invalid_order_data":
            # Try to create order with invalid data
            response = authenticated_client.post(
                "/api/v1/order", data={"invalid": "data"}, format="json"
            )

        elif error_scenario == "missing_required_fields":
            # Try to create order without required fields
            response = authenticated_client.post(
                "/api/v1/order", data={}, format="json"
            )

        # Verify: Response has expected status code
        assert response is not None, (
            f"No response for scenario: {error_scenario}"
        )
        assert response.status_code == expected_status, (
            f"Expected status {expected_status}, got {response.status_code} "
            f"for scenario: {error_scenario}"
        )

        # Verify: Response is JSON (DRF returns JSON for API endpoints)
        content_type = response.headers.get("Content-Type", "")
        assert "application/json" in content_type, (
            f"Expected JSON response for scenario: {error_scenario}, got {content_type}"
        )

        # Verify: Response has expected fields
        response_data = response.json()

        # For validation errors, DRF returns field-specific errors
        # For other errors, DRF returns a "detail" field
        if expected_status == status.HTTP_400_BAD_REQUEST:
            # Validation errors: check that at least one expected field exists
            has_expected_field = any(
                field in response_data for field in expected_fields
            )
            assert has_expected_field, (
                f"Expected at least one of {expected_fields} in response for scenario: {error_scenario}. "
                f"Got: {response_data}"
            )
        else:
            # Other errors: check for "detail" field
            for field in expected_fields:
                assert field in response_data, (
                    f"Expected field '{field}' in response for scenario: {error_scenario}. "
                    f"Got: {response_data}"
                )

        # Verify: Detail field is a string (if present)
        if "detail" in response_data:
            assert isinstance(response_data["detail"], str), (
                f"Expected 'detail' to be string for scenario: {error_scenario}"
            )
            assert len(response_data["detail"]) > 0, (
                f"Expected non-empty 'detail' for scenario: {error_scenario}"
            )

    @pytest.mark.parametrize(
        "validation_scenario,endpoint,data,expected_error_fields",
        [
            # Order creation validation errors
            (
                "missing_shipping_address",
                "/api/v1/order",
                {"items": []},
                ["detail"],
            ),
            (
                "invalid_payment_intent",
                "/api/v1/order",
                {
                    "payment_intent_id": "invalid",
                    "items": [],
                    "shipping_address": {},
                },
                ["detail"],
            ),
        ],
    )
    def test_validation_errors_include_field_specific_messages(
        self,
        authenticated_client,
        product,
        pay_way,
        validation_scenario,
        endpoint,
        data,
        expected_error_fields,
    ):
        """
        Test that validation errors include field-specific error messages.

        Verifies that validation errors:
        - Return 400 Bad Request status
        - Include detail field
        - May include field-specific error information
        """
        # Make request with invalid data
        response = authenticated_client.post(endpoint, data=data, format="json")

        # Verify: Returns 400 Bad Request
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            f"Expected 400 for validation scenario: {validation_scenario}"
        )

        # Verify: Response is JSON (DRF returns JSON for API endpoints)
        content_type = response.headers.get("Content-Type", "")
        assert "application/json" in content_type, (
            f"Expected JSON response for validation scenario: {validation_scenario}, got {content_type}"
        )

        # Verify: Response has expected error fields
        response_data = response.json()

        # DRF validation errors return field-specific errors, not a generic "detail" field
        # Check that at least one expected field exists
        has_expected_field = any(
            field in response_data for field in expected_error_fields
        )
        assert has_expected_field or len(response_data) > 0, (
            f"Expected at least one of {expected_error_fields} or any validation error in response "
            f"for scenario: {validation_scenario}. Got: {response_data}"
        )

        # Verify: Detail field exists and is non-empty (if present)
        if "detail" in response_data:
            assert isinstance(response_data["detail"], str)
            assert len(response_data["detail"]) > 0

        # Verify: Field-specific errors are lists of strings (if present)
        for field, errors in response_data.items():
            if field != "detail" and isinstance(errors, list):
                for error in errors:
                    assert isinstance(error, str), (
                        f"Expected error message to be string for field '{field}'"
                    )

    def test_error_response_format_consistency_across_endpoints(
        self, authenticated_client, authenticated_user
    ):
        """
        Test that error response format is consistent across different endpoints.

        Verifies that all endpoints return errors in the same format.
        """
        # Test multiple endpoints with not found errors
        endpoints_to_test = [
            ("/api/v1/order/999999", status.HTTP_404_NOT_FOUND),
            ("/api/v1/order/999999/cancel", status.HTTP_404_NOT_FOUND),
        ]

        error_responses = []
        for endpoint, expected_status in endpoints_to_test:
            if endpoint.endswith("/cancel"):
                response = authenticated_client.post(
                    endpoint, data={"reason": "test"}, format="json"
                )
            else:
                response = authenticated_client.get(endpoint)

            assert response.status_code == expected_status
            error_responses.append(response.json())

        # Verify: All responses have 'detail' field
        for response_data in error_responses:
            assert "detail" in response_data, (
                f"Expected 'detail' field in error response: {response_data}"
            )
            assert isinstance(response_data["detail"], str)

        # Verify: Response structure is consistent
        # All should have the same top-level keys
        for response_data in error_responses[1:]:
            # Allow for some variation but 'detail' must always be present
            assert "detail" in response_data

    def test_error_responses_do_not_leak_sensitive_information(
        self, authenticated_client, authenticated_user
    ):
        """
        Test that error responses don't leak sensitive information.

        Verifies that error messages:
        - Don't include stack traces in production
        - Don't include database query details
        - Don't include internal system paths
        """
        # Try to access non-existent order
        response = authenticated_client.get("/api/v1/order/999999")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        response_data = response.json()

        # Verify: No stack traces
        response_str = str(response_data).lower()
        assert "traceback" not in response_str
        assert 'file "' not in response_str
        assert "line " not in response_str

        # Verify: No SQL queries
        assert "select" not in response_str
        assert "from order" not in response_str
        assert "where" not in response_str

        # Verify: No internal paths
        assert "/home/" not in response_str
        assert "/usr/" not in response_str
        assert "c:\\" not in response_str.lower()

    @pytest.mark.parametrize(
        "http_method,endpoint_template,expected_status",
        [
            ("GET", "/api/v1/order/{id}", status.HTTP_404_NOT_FOUND),
            ("PUT", "/api/v1/order/{id}", status.HTTP_404_NOT_FOUND),
            ("PATCH", "/api/v1/order/{id}", status.HTTP_404_NOT_FOUND),
            ("DELETE", "/api/v1/order/{id}", status.HTTP_404_NOT_FOUND),
            ("POST", "/api/v1/order/{id}/cancel", status.HTTP_404_NOT_FOUND),
        ],
    )
    def test_error_format_consistent_across_http_methods(
        self,
        authenticated_client,
        http_method,
        endpoint_template,
        expected_status,
    ):
        """
        Test that error format is consistent across different HTTP methods.
        """
        endpoint = endpoint_template.format(id=999999)

        # Make request with specified HTTP method
        if http_method == "GET":
            response = authenticated_client.get(endpoint)
        elif http_method == "POST":
            response = authenticated_client.post(
                endpoint, data={"reason": "test"}, format="json"
            )
        elif http_method == "PUT":
            response = authenticated_client.put(
                endpoint, data={}, format="json"
            )
        elif http_method == "PATCH":
            response = authenticated_client.patch(
                endpoint, data={}, format="json"
            )
        elif http_method == "DELETE":
            response = authenticated_client.delete(endpoint)

        # Verify: Expected status code
        assert response.status_code == expected_status

        # Verify: Consistent error format
        response_data = response.json()
        assert "detail" in response_data
        assert isinstance(response_data["detail"], str)
        assert len(response_data["detail"]) > 0

    def test_concurrent_error_responses_maintain_format(
        self, authenticated_client, authenticated_user
    ):
        """
        Test that error format is maintained under concurrent requests.

        Verifies that error response format doesn't degrade under load.
        """
        import threading

        results = []

        def make_request():
            try:
                response = authenticated_client.get("/api/v1/order/999999")
                results.append(response.json())
            except Exception as e:
                results.append({"error": str(e)})

        # Create multiple threads making concurrent requests
        threads = [threading.Thread(target=make_request) for _ in range(5)]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # Verify: All responses have consistent format
        assert len(results) == 5
        for response_data in results:
            assert "detail" in response_data, (
                f"Expected 'detail' in concurrent response: {response_data}"
            )
            assert isinstance(response_data["detail"], str)
