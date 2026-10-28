"""``ws/notifications/`` refusals through the real ASGI stack.

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

from asgi import application
from tenant.models import TenantDomain
from tests.utils.staff import store_tenant

HOST = b"shop.example"
ORIGIN = b"https://shop.example"


def _communicator(host: bytes) -> ApplicationCommunicator:
    return ApplicationCommunicator(
        application,
        {
            "type": "websocket",
            "path": "/ws/notifications/",
            "query_string": b"",
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
