from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class NuxtPurgeResult:
    matched: int
    deleted: int
    blocked: int
    error: str | None = None


def _resolve_endpoint() -> str | None:
    """Return the absolute Nuxt purge URL or ``None`` if not configured."""

    base = getattr(settings, "NUXT_INTERNAL_BASE_URL", "") or ""
    if not base:
        return None
    return f"{base.rstrip('/')}/api/admin/cache/purge"


def _resolve_token() -> str | None:
    return getattr(settings, "NUXT_CACHE_PURGE_TOKEN", "") or None


def is_configured() -> bool:
    return bool(_resolve_endpoint() and _resolve_token())


def request_purge(
    patterns: list[str], *, dry_run: bool = False, timeout: float = 5.0
) -> NuxtPurgeResult:
    """Call the Nuxt purge endpoint with the supplied glob patterns.

    Returns a structured result; never raises so admin actions stay
    responsive when the frontend is briefly unreachable.
    """

    if not patterns:
        return NuxtPurgeResult(matched=0, deleted=0, blocked=0)

    endpoint = _resolve_endpoint()
    token = _resolve_token()
    if not endpoint or not token:
        msg = "Nuxt purge endpoint is not configured"
        logger.warning(msg)
        return NuxtPurgeResult(matched=0, deleted=0, blocked=0, error=msg)

    payload = {"patterns": patterns, "dryRun": dry_run}
    headers = {
        "X-Cache-Purge-Token": token,
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            endpoint, json=payload, headers=headers, timeout=timeout
        )
        response.raise_for_status()
        data = response.json()
        return NuxtPurgeResult(
            matched=int(data.get("matched", 0)),
            deleted=int(data.get("deleted", 0)),
            blocked=int(data.get("blocked", 0)),
        )
    except requests.RequestException as exc:
        logger.warning("Nuxt cache purge failed: %s", exc)
        return NuxtPurgeResult(matched=0, deleted=0, blocked=0, error=str(exc))
