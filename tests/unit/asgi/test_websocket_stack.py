"""``ws/notifications/`` refusals through the real ASGI stack.

``asgi.application`` wraps the consumer in the origin validator, the
tenant gate and the ticket auth, so these connect exactly as a browser
does rather than calling one layer in isolation.
"""

from __future__ import annotations

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase

from asgi import application
from tenant.models import TenantDomain
from tests.utils.staff import store_tenant

HOST = b"shop.example"
ORIGIN = b"https://shop.example"


def _communicator(host: bytes) -> WebsocketCommunicator:
    return WebsocketCommunicator(
        application,
        "/ws/notifications/",
        headers=[(b"host", host), (b"origin", ORIGIN)],
    )


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

        connected, _ = await communicator.connect()

        assert connected is True
        assert await communicator.receive_output() == {
            "type": "websocket.close",
            "code": 4003,
        }

    async def test_unknown_host_is_refused_before_the_handshake(self):
        communicator = _communicator(b"other.example")

        connected, code = await communicator.connect()

        assert connected is False
        assert code == 4004
