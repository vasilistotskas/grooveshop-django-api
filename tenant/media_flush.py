"""Per-tenant media flush against the media-stream service.

Posts media-stream's ``/admin/cache/flush-tenant`` (guarded by
``x-internal-secret``), which on every replica first re-reads the tenant
feed (``tenant/internal.py``: ``suspendedSchemas``) and then drops the
tenant's cached copies. media-stream refuses a suspended store's images
by that state (503) before reading any cache, so the flush is what makes
a suspension or a reactivation apply at once rather than at media-stream's
next poll (``TENANT_DOMAINS_REFRESH_INTERVAL_MS``, 5 minutes). The cache
drop keeps a destroyed tenant's copies from outliving it, up to 180 days
private, 360 days public.

Called through ``tenant.tasks.flush_tenant_media_task`` so a slow or
down media-stream never blocks a suspension and transient failures
retry. Fail-open by configuration: with no internal URL/secret set the
flush is skipped and the cache expires by TTL.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def flush_tenant_media(schema_name: str, *, timeout: float = 5.0) -> bool:
    """POST a per-tenant flush to media-stream. Returns True on success.

    Returns False (skip) when the internal URL/secret is unconfigured.
    Raises ``requests.RequestException`` on an HTTP/network failure so
    the calling Celery task's autoretry can act; the task, not this
    function, owns the eventual give-up + logging.
    """
    base = getattr(settings, "MEDIA_STREAM_INTERNAL_URL", "") or ""
    secret = getattr(settings, "MEDIA_STREAM_INTERNAL_SECRET", "") or ""
    if not base or not secret:
        logger.info(
            "media flush skipped for %s — MEDIA_STREAM_INTERNAL_URL/"
            "SECRET not configured",
            schema_name,
        )
        return False

    endpoint = f"{base.rstrip('/')}/admin/cache/flush-tenant"
    response = requests.post(
        endpoint,
        json={"tenantSchema": schema_name},
        headers={
            "x-internal-secret": secret,
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    logger.info("media flush ok for tenant schema %s", schema_name)
    return True
