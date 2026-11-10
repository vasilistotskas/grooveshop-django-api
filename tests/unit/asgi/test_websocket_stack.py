"""``ws/notifications/`` through the real ASGI stack: its refusals, and the
heartbeat a signed-in socket is answered.

``asgi.application`` wraps the consumer in the origin validator, the
tenant gate and the ticket auth, so these connect exactly as a browser
does rather than calling one layer in isolation.

The tests drive the application with ``asgiref``'s
``ApplicationCommunicator`` and the raw ASGI websocket events. Channels'
``WebsocketCommunicator`` is a thin wrapper over it, but importing
``channels.testing`` pulls in ``daphne.testing``, and daphne (with
Twisted) is not a dependency of this project.
"""

from __future__ import annotations

from typing import Any

from asgiref.testing import ApplicationCommunicator
from channels.db import database_sync_to_async
from django.test import TransactionTestCase
from django_tenants.utils import tenant_context
from knox.models import get_token_model

from asgi import application
from core import tickets
from tenant.models import TenantDomain
from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory

HOST = b"shop.example"
ORIGIN = b"https://shop.example"


def _communicator(
    host: bytes, query_string: bytes = b""
) -> ApplicationCommunicator:
    return ApplicationCommunicator(
        application,
        {
            "type": "websocket",
            "path": "/ws/notifications/",
            "query_string": query_string,
            "headers": [(b"host", host), (b"origin", ORIGIN)],
            "subprotocols": [],
        },
    )


async def _connect(
    communicator: ApplicationCommunicator,
) -> tuple[bool, dict[str, Any]]:
    """Send ``websocket.connect`` and report whether the handshake was
    accepted, with the first event the application answered."""
    await communicator.send_input({"type": "websocket.connect"})
    event = await communicator.receive_output(timeout=1)
    return event["type"] == "websocket.accept", event


class WebsocketStackRefusalTestCase(TransactionTestCase):
    """The tenant lookup runs under ``database_sync_to_async``, so the
    domain row must be really committed."""

    @database_sync_to_async
    def _store(self):
        TenantDomain.objects.create(
            tenant=store_tenant("ws_stack"), domain="shop.example"
        )

    async def test_no_ticket_is_accepted_then_closed_as_unauthenticated(
        self,
    ):
        """The consumer completes the handshake so the application close
        code reaches the client, then closes with 4003."""
        await self._store()
        communicator = _communicator(HOST)

        connected, _ = await _connect(communicator)

        assert connected is True
        assert await communicator.receive_output(timeout=1) == {
            "type": "websocket.close",
            "code": 4003,
        }

    async def test_unknown_host_is_refused_before_the_handshake(self):
        communicator = _communicator(b"other.example")

        connected, event = await _connect(communicator)

        assert connected is False
        assert event["type"] == "websocket.close"
        assert event["code"] == 4004


class WebsocketStackHeartbeatTestCase(TransactionTestCase):
    """A signed-in socket, connected the way the storefront connects: a
    ticket minted for the store, spent by the ticket auth, then the
    consumer. The storefront pings every 30 seconds and closes a socket
    that does not answer within 5."""

    @database_sync_to_async
    def _signed_in_ticket(self) -> str:
        tenant = store_tenant("ws_heartbeat")
        TenantDomain.objects.create(tenant=tenant, domain="shop.example")
        with tenant_context(tenant):
            user = UserAccountFactory()
            get_token_model().objects.create(user)
            return tickets.mint(tickets.WEBSOCKET, user.pk, 60)

    async def test_a_ping_is_answered_with_pong_and_nothing_else_is(self):
        ticket = await self._signed_in_ticket()
        communicator = _communicator(HOST, f"ticket={ticket}".encode())

        connected, _ = await _connect(communicator)
        assert connected is True

        await communicator.send_input(
            {"type": "websocket.receive", "text": "ping"}
        )
        assert await communicator.receive_output(timeout=1) == {
            "type": "websocket.send",
            "text": "pong",
        }

        await communicator.send_input(
            {"type": "websocket.receive", "text": '{"hello": "server"}'}
        )
        assert await communicator.receive_nothing(timeout=0.3) is True

        await communicator.send_input(
            {"type": "websocket.disconnect", "code": 1000}
        )
        await communicator.wait(timeout=1)
