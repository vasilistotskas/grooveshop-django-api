"""``POST /api/v1/websocket/ticket``: the ticket the socket spends."""

from __future__ import annotations

import pytest
from django.urls import reverse
from knox.models import get_token_model
from rest_framework import status
from rest_framework.test import APIClient

from core import tickets
from notification.views.websocket import WS_TICKET_TTL_SECONDS
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def mint_as(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.post(reverse("websocket-ticket"))


def test_a_signed_in_customer_gets_a_ticket_the_socket_can_spend_once():
    user = UserAccountFactory()
    get_token_model().objects.create(user=user)

    response = mint_as(user)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["expiresIn"] == WS_TICKET_TTL_SECONDS
    assert tickets.spend(tickets.WEBSOCKET, body["ticket"]) == user.pk
    assert tickets.spend(tickets.WEBSOCKET, body["ticket"]) is None


def test_no_live_session_gets_none():
    user = UserAccountFactory()

    response = mint_as(user)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["code"] == "session_required"


def test_a_visitor_gets_none():
    response = APIClient().post(reverse("websocket-ticket"))

    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )
