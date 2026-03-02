from __future__ import annotations

import logging
from urllib.parse import urlparse

from channels.db import database_sync_to_async
from django.db import connection

from tenant.models import TenantDomain

logger = logging.getLogger(__name__)


class TenantWebsocketMiddleware:
    """Resolve tenant from WebSocket Host header and set schema."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        host_header = headers.get(b"host", b"").decode("utf-8")

        # Strip port if present
        host = urlparse(f"http://{host_header}").hostname or host_header

        tenant = await self._get_tenant(host)
        if tenant is None:
            logger.warning(f"WebSocket: No tenant found for host {host}")
            await send({"type": "websocket.close", "code": 4004})
            return

        scope["tenant"] = tenant
        connection.set_tenant(tenant)
        await self.inner(scope, receive, send)

    @database_sync_to_async
    def _get_tenant(self, host):
        domain = (
            TenantDomain.objects.select_related("tenant")
            .filter(domain=host, tenant__is_active=True)
            .first()
        )
        return domain.tenant if domain else None
