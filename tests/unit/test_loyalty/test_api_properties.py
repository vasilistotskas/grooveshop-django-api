"""Property-based tests for Loyalty API authentication.

Feature: ranking-and-loyalty-points
Tests Property 19 from the design document.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from rest_framework import status
from rest_framework.test import APIClient

# Feature: ranking-and-loyalty-points, Property 19: Unauthenticated access returns 401
# **Validates: Requirements 10.5**

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# All loyalty API endpoints with their HTTP methods and URL paths.
# For the product_points endpoint, we use a fixed product ID since the
# authentication check happens before any product lookup.
loyalty_endpoints = st.sampled_from(
    [
        ("get", "/api/v1/loyalty/summary"),
        ("get", "/api/v1/loyalty/transactions"),
        ("post", "/api/v1/loyalty/redeem"),
        ("get", "/api/v1/loyalty/product/1/points"),
    ]
)


# ===========================================================================
# Property 19: Unauthenticated access returns 401
# Feature: ranking-and-loyalty-points, Property 19: Unauthenticated access returns 401
# ===========================================================================


@pytest.mark.django_db
class TestUnauthenticatedAccessReturns401:
    """**Validates: Requirements 10.5**"""

    @given(endpoint=loyalty_endpoints)
    @settings(max_examples=100, deadline=None)
    def test_unauthenticated_request_returns_401(
        self,
        endpoint: tuple[str, str],
    ):
        """For any loyalty API endpoint, a request without authentication
        credentials shall receive a 401 Unauthorized response.

        **Validates: Requirements 10.5**
        """
        method, url = endpoint
        client = APIClient()

        if method == "get":
            response = client.get(url)
        elif method == "post":
            response = client.post(url, data={}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"Expected 401 for unauthenticated {method.upper()} {url}, "
            f"got {response.status_code}"
        )
