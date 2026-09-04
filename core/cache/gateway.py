"""Feed-cache invalidation on the agent gateway.

The catalog feeds (Google / Meta / TikTok / ACP) are generated and
gzipped by the Go gateway and cached in ITS Redis under
``ag:{schema}:feed:{kind}`` with ``FEED_FRESH_TTL`` (6h by default).

That cache is unreachable from here by any other means:

* it lives in a different Redis logical DB than Django's cache, so
  ``CustomCache.clear_by_prefixes`` cannot see it;
* it survives gateway pod restarts, so a rollout does not clear it.

So before this module the only ways out of a stale feed were to wait six
hours or delete the keys by hand — meaning a merchant's price change, a
new product or a stock change took up to six hours to reach Google, Meta
and TikTok. Observed on staging: the feeds served 7 items for a while
after the catalogue held 35.

Mirrors ``core.cache.nuxt``: same "never raise, return a structured
result" contract, because this is called from admin actions and a
management command where an unreachable sidecar must not take the purge
down.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class GatewayPurgeResult:
    removed: int
    error: str | None = None


def _resolve_endpoint() -> str | None:
    base = getattr(settings, "AGENT_GATEWAY_INTERNAL_URL", "") or ""
    if not base:
        return None
    return f"{base.rstrip('/')}/internal/feeds/invalidate"


def _resolve_token() -> str | None:
    return getattr(settings, "AGENT_GATEWAY_INTERNAL_SECRET", "") or None


def is_configured() -> bool:
    return bool(_resolve_endpoint() and _resolve_token())


def invalidate_feeds(
    schema_name: str | None = None, *, timeout: float | None = None
) -> GatewayPurgeResult:
    """Drop the gateway's cached feeds for one tenant schema.

    ``schema_name`` defaults to the active connection's schema, which is
    what a merchant purge running inside ``schema_context`` wants. The
    gateway requires it explicitly rather than inferring anything —
    guessing would drop another tenant's feeds.
    """
    from django.db import connection

    schema = schema_name or getattr(connection, "schema_name", None)
    if not schema:
        return GatewayPurgeResult(removed=0, error="no active schema")

    endpoint = _resolve_endpoint()
    token = _resolve_token()
    if not endpoint or not token:
        msg = "Agent gateway internal endpoint is not configured"
        logger.info(msg)
        return GatewayPurgeResult(removed=0, error=msg)

    try:
        response = requests.post(
            endpoint,
            json={"schemaName": schema},
            headers={
                "X-Internal-Token": token,
                "Content-Type": "application/json",
            },
            timeout=timeout or settings.AGENT_GATEWAY_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        return GatewayPurgeResult(
            removed=int(response.json().get("removed", 0))
        )
    except requests.RequestException as exc:
        logger.warning("Gateway feed invalidation failed: %s", exc)
        return GatewayPurgeResult(removed=0, error=str(exc))
    except (ValueError, TypeError) as exc:
        # 2xx with a body we cannot read: the keys are almost certainly
        # gone, but report it rather than claim a count we do not have.
        logger.warning("Gateway feed invalidation returned junk: %s", exc)
        return GatewayPurgeResult(removed=0, error=str(exc))
