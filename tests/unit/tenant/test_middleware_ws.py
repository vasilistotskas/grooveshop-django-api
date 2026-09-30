"""The WebSocket tenant gate (``tenant/middleware_ws.py``).

A socket is admitted only on a host that belongs to an active store:
an unknown or deactivated host is closed with ``4004`` before the auth
stack or the consumer ever runs, and an admitted socket carries its
tenant on ``scope["tenant"]`` (the consumer reads it from there, never
from the thread-local connection).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from channels.db import database_sync_to_async
from django.test import TransactionTestCase

from tenant.middleware_ws import TenantWebsocketMiddleware
from tenant.models import TenantDomain
from tests.utils.staff import store_tenant


def _scope(host: bytes | None) -> dict:
    headers = [(b"origin", b"https://shop.example")]
    if host is not None:
        headers.append((b"host", host))
    return {
        "type": "websocket",
        "path": "/ws/notifications/",
        "headers": headers,
    }


class TenantWebsocketMiddlewareTestCase(TransactionTestCase):
    """The tenant lookup runs under ``database_sync_to_async``, whose
    ``close_old_connections()`` closes a connection held inside a test
    transaction, so these rows must be really committed."""

    @database_sync_to_async
    def _store(self, *, active=True):
        tenant = store_tenant("ws_gate", is_active=active)
        TenantDomain.objects.create(
            tenant=tenant, domain="shop.example", is_primary=True
        )
        return tenant

    async def _call(self, scope):
        inner = AsyncMock()
        send = AsyncMock()
        await TenantWebsocketMiddleware(inner)(scope, AsyncMock(), send)
        return inner, send

    async def test_unknown_host_is_closed_with_4004(self):
        await self._store()

        inner, send = await self._call(_scope(b"other.example"))

        send.assert_awaited_once_with({"type": "websocket.close", "code": 4004})
        inner.assert_not_awaited()

    async def test_missing_host_header_is_closed_with_4004(self):
        inner, send = await self._call(_scope(None))

        send.assert_awaited_once_with({"type": "websocket.close", "code": 4004})
        inner.assert_not_awaited()

    async def test_deactivated_store_is_closed_with_4004(self):
        await self._store(active=False)

        inner, send = await self._call(_scope(b"shop.example"))

        send.assert_awaited_once_with({"type": "websocket.close", "code": 4004})
        inner.assert_not_awaited()

    async def test_known_host_puts_the_tenant_on_the_scope(self):
        """The port is stripped before the lookup, and the socket goes
        on to the inner stack untouched apart from ``scope["tenant"]``."""
        tenant = await self._store()
        scope = _scope(b"shop.example:8443")

        inner, send = await self._call(scope)

        send.assert_not_awaited()
        inner.assert_awaited_once()
        passed_scope = inner.await_args.args[0]
        assert passed_scope is scope
        assert passed_scope["tenant"].pk == tenant.pk
