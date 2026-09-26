"""Purge storefront pages from the Cloudflare edge cache.

The storefront lets Cloudflare cache only the pages Nitro serves from its
own page cache, and tags each one (``server/utils/edgeCache.ts`` in the
storefront): ``storefront-html`` on every page, ``storefront-html-<schema>``
on each tenant's. Those tag names are a contract with that file.

A page is purged here after Nitro's copy has been purged, so the edge
refetches the fresh render rather than the entry that was just evicted.

Which zone a tenant's pages live in, and whose token purges them:

* A store on its OWN domain proxied through its OWN Cloudflare account
  (tenant #1's ``webside.gr``) sets ``Tenant.cloudflare_zone_id`` and
  ``Tenant.cloudflare_api_token``. Tenant-only, like every merchant
  credential: nothing else can purge that account.
* A store on a platform hostname (``*.grooveshop.space``) lives in the
  platform's zone, which the platform purges with its own credentials
  (``CLOUDFLARE_PLATFORM_*`` settings).

A store matching neither is not behind a Cloudflare cache of ours, and
there is nothing to purge.

Cloudflare's Free plan allows five purge requests a minute per account,
so a merchant's edits are coalesced into one purge per tenant
(``schedule_tenant_purge``) rather than sent one save at a time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import connection

logger = logging.getLogger(__name__)

EDGE_CACHE_TAG = "storefront-html"

#: How long a merchant's edits are gathered before one purge is sent.
PURGE_DELAY_SECONDS = 30

_API = "https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache"
_TIMEOUT_SECONDS = 10


def tenant_tag(schema_name: str) -> str:
    return f"{EDGE_CACHE_TAG}-{schema_name}"


@dataclass(frozen=True)
class Zone:
    zone_id: str
    api_token: str


class EdgePurgeError(Exception):
    """Cloudflare refused or could not be reached; the caller may retry."""


def platform_zone() -> Zone | None:
    zone_id = settings.CLOUDFLARE_PLATFORM_ZONE_ID
    api_token = settings.CLOUDFLARE_PLATFORM_API_TOKEN
    if not (zone_id and api_token and settings.CLOUDFLARE_PLATFORM_ZONE_NAME):
        return None
    return Zone(zone_id=zone_id, api_token=api_token)


def _on_platform_zone(domain: str) -> bool:
    zone_name = settings.CLOUDFLARE_PLATFORM_ZONE_NAME.lower()
    domain = domain.lower()
    return domain == zone_name or domain.endswith(f".{zone_name}")


def tenant_zones(tenant) -> list[Zone]:
    """The Cloudflare zones that hold this tenant's storefront pages."""
    zones: list[Zone] = []
    if tenant.cloudflare_zone_id and tenant.cloudflare_api_token:
        zones.append(
            Zone(
                zone_id=tenant.cloudflare_zone_id,
                api_token=tenant.cloudflare_api_token,
            )
        )
    platform = platform_zone()
    if platform and any(
        _on_platform_zone(domain)
        for domain in tenant.domains.values_list("domain", flat=True)
    ):
        zones.append(platform)
    return zones


def purge_tags(zone: Zone, tags: list[str]) -> None:
    """Purge ``tags`` from one zone. Raises ``EdgePurgeError`` on failure."""
    try:
        response = requests.post(
            _API.format(zone_id=zone.zone_id),
            json={"tags": tags},
            headers={"Authorization": f"Bearer {zone.api_token}"},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise EdgePurgeError(f"Cloudflare unreachable: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or not body.get("success"):
        errors = body.get("errors") or response.text[:200]
        raise EdgePurgeError(
            f"Cloudflare purge of zone {zone.zone_id} answered "
            f"{response.status_code}: {errors}"
        )


def purge_tenant(tenant) -> int:
    """Purge one tenant's pages from every zone that holds them.

    Returns the number of zones purged.
    """
    zones = tenant_zones(tenant)
    for zone in zones:
        purge_tags(zone, [tenant_tag(tenant.schema_name)])
    return len(zones)


def purge_all_storefronts() -> int:
    """Purge every storefront page from every zone (after a deploy).

    One request per zone: the platform zone, and each store's own. Returns
    the number of zones purged.
    """
    from tenant.models import Tenant

    zones: dict[str, Zone] = {}
    platform = platform_zone()
    if platform:
        zones[platform.zone_id] = platform
    own = (
        Tenant.objects.exclude(cloudflare_zone_id="")
        .exclude(cloudflare_api_token="")
        .values_list("cloudflare_zone_id", "cloudflare_api_token")
    )
    for zone_id, api_token in own:
        zones.setdefault(zone_id, Zone(zone_id=zone_id, api_token=api_token))
    for zone in zones.values():
        purge_tags(zone, [EDGE_CACHE_TAG])
    return len(zones)


def _pending_key(schema_name: str) -> str:
    return f"edge-cache-purge-pending:{schema_name}"


def schedule_purge() -> bool:
    """Queue one edge purge for the current schema, coalescing repeats.

    In a tenant's schema it purges that store's pages; on the public
    schema (a platform-wide Nitro purge) it purges every storefront. Every
    change inside ``PURGE_DELAY_SECONDS`` rides the purge already queued:
    the flag is cleared when the task STARTS, so an edit made while it
    runs queues the next one. Returns whether a task was queued.
    """
    schema_name = connection.schema_name
    if not cache.add(
        _pending_key(schema_name), True, timeout=PURGE_DELAY_SECONDS * 4
    ):
        return False
    from core.tasks import purge_edge_cache_task

    purge_edge_cache_task.apply_async(countdown=PURGE_DELAY_SECONDS)
    return True


def run_scheduled_purge() -> int:
    """The queued purge, run in the schema that queued it."""
    cache.delete(_pending_key(connection.schema_name))
    if connection.schema_name == "public":
        return purge_all_storefronts()
    return purge_tenant(connection.tenant)
