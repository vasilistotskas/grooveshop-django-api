"""A guest reaches their receipt with the order's private uuid, exactly as
they reach the order itself, and the uuid opens nothing that belongs to an
account."""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from order.factories.order import OrderFactory
from tests.integration.order.test_cancel_and_invoice_views import (
    _rendered_receipt,
)
from tests.utils.mydata import issuable
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

ROUTES = ["order-invoice", "order-invoice-download"]


@pytest.fixture
def guest_client():
    return APIClient()


@pytest.mark.parametrize("route", ROUTES)
def test_a_guest_with_the_orders_uuid_gets_the_receipt(
    guest_client, route
) -> None:
    order, _invoice = _rendered_receipt(None)

    response = guest_client.get(
        reverse(route, kwargs={"pk": order.pk}), {"uuid": str(order.uuid)}
    )

    assert response.status_code == status.HTTP_200_OK


def test_the_metadata_download_link_carries_the_guests_uuid(
    guest_client,
) -> None:
    order, _invoice = _rendered_receipt(None)

    link = guest_client.get(
        reverse("order-invoice", kwargs={"pk": order.pk}),
        {"uuid": str(order.uuid)},
    ).json()["downloadUrl"]

    assert f"uuid={order.uuid}" in link
    assert guest_client.get(link).status_code == status.HTTP_200_OK


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("query", [{}, {"uuid": str(uuid4())}])
def test_a_guest_without_the_right_uuid_is_refused(
    guest_client, route, query
) -> None:
    order, _invoice = _rendered_receipt(None)

    response = guest_client.get(reverse(route, kwargs={"pk": order.pk}), query)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize("route", ROUTES)
def test_a_uuid_does_not_open_an_account_orders_receipt(
    guest_client, route
) -> None:
    order, _invoice = _rendered_receipt(UserAccountFactory())

    response = guest_client.get(
        reverse(route, kwargs={"pk": order.pk}), {"uuid": str(order.uuid)}
    )

    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


@pytest.mark.parametrize("route", ROUTES)
def test_the_owner_still_reaches_their_receipt(route) -> None:
    user = UserAccountFactory()
    order, _invoice = _rendered_receipt(user)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get(reverse(route, kwargs={"pk": order.pk}))

    assert response.status_code == status.HTTP_200_OK


def test_a_guest_with_the_uuid_gets_404_while_no_receipt_is_registered(
    guest_client,
) -> None:
    order = issuable(OrderFactory(user=None))

    response = guest_client.get(
        reverse("order-invoice", kwargs={"pk": order.pk}),
        {"uuid": str(order.uuid)},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
