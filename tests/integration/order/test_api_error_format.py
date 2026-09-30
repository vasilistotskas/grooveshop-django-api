"""The order endpoints answer errors the way the storefront reads them.

Lookup and permission failures carry a non-empty ``detail`` string, and
the order that decides 403 vs 404 is part of the contract: a non-staff
``DELETE`` is refused before the object lookup, so it never reveals
whether the order exists. Validation failures are per-field lists under
camelCase keys (``djangorestframework-camel-case``).
"""

import uuid

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from order.factories import OrderFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def customer_client():
    client = APIClient()
    client.force_authenticate(user=UserAccountFactory(num_addresses=0))
    return client


def _someone_elses_order_path():
    other = UserAccountFactory(num_addresses=0)
    order = OrderFactory(user=other, num_order_items=0)
    return f"/api/v1/order/{order.id}"


@pytest.mark.parametrize(
    "method,path,expected_status",
    [
        ("get", "/api/v1/order/999999", status.HTTP_404_NOT_FOUND),
        ("post", "/api/v1/order/999999/cancel", status.HTTP_404_NOT_FOUND),
        ("put", "/api/v1/order/999999", status.HTTP_404_NOT_FOUND),
        ("patch", "/api/v1/order/999999", status.HTTP_404_NOT_FOUND),
        ("delete", "/api/v1/order/999999", status.HTTP_403_FORBIDDEN),
        ("get", _someone_elses_order_path, status.HTTP_403_FORBIDDEN),
    ],
    ids=[
        "missing",
        "cancel-missing",
        "put-missing",
        "patch-missing",
        "delete-not-staff",
        "not-owner",
    ],
)
def test_lookup_and_permission_errors_carry_a_detail(
    customer_client, method, path, expected_status
):
    if callable(path):
        path = path()

    response = getattr(customer_client, method)(path, format="json")

    assert response.status_code == expected_status
    assert "application/json" in response.headers["Content-Type"]
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert detail


def test_unknown_uuid_is_a_404_for_a_guest():
    response = APIClient().get(f"/api/v1/order/uuid/{uuid.uuid4()}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"]


def test_validation_errors_are_per_field_lists_under_camel_case_keys(
    customer_client,
):
    response = customer_client.post("/api/v1/order", {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert "payWayId" in body
    assert "pay_way_id" not in body
    for messages in body.values():
        assert isinstance(messages, list)
        assert all(isinstance(message, str) for message in messages)
