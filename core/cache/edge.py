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

Processed images are the other kind of edge content. media-stream serves
them for a year (``s-maxage``) and without a tag, so a suspended or
destroyed tenant's images would outlive media-stream's own flush at the
edge. They are purged by URL prefix (``tenant_media_purges``,
``purge_media``), which needs no tag and also reaches images cached before
the purge existed.

Cloudflare's Free plan allows five purge requests a minute per account,
so a merchant's edits are coalesced into one purge per tenant
(``schedule_tenant_purge``) rather than sent one save at a time.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypedDict
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import connection

from tenant.membership import bound_tenant

logger = logging.getLogger(__name__)

EDGE_CACHE_TAG = "storefront-html"

#: media-stream's route under every assets host, ending where the tenant
#: schema starts (``media/:tenantSchema/uploads/…`` below ``/media_stream-image``).
#: A contract with media-stream's ``IMAGE_SOURCES``.
MEDIA_PATH = "media_stream-image/media"

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


def _purge(zone: Zone, selector: dict[str, list[str]]) -> None:
    """Send one purge request. Raises ``EdgePurgeError`` on failure."""
    try:
        response = requests.post(
            _API.format(zone_id=zone.zone_id),
            json=selector,
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


def purge_tags(zone: Zone, tags: list[str]) -> None:
    """Purge ``tags`` from one zone. Raises ``EdgePurgeError`` on failure."""
    _purge(zone, {"tags": tags})


def purge_prefixes(zone: Zone, prefixes: list[str]) -> None:
    """Purge every cached URL under ``prefixes`` (host and path, no scheme).

    Raises ``EdgePurgeError`` on failure.
    """
    _purge(zone, {"prefixes": prefixes})


class MediaPurge(TypedDict):
    """The prefixes of one tenant's images in one zone.

    JSON-native on purpose: it travels as a Celery argument, so it names the
    zone but never carries that zone's API token.
    """

    zone_id: str
    prefixes: list[str]


def _media_prefix(host: str, schema_name: str) -> str:
    return f"{host.lower()}/{MEDIA_PATH}/{schema_name}/"


def tenant_media_purges(tenant) -> list[MediaPurge]:
    """Where a tenant's processed images are cached at the Cloudflare edge.

    Every tenant's images are served from the platform assets host
    (``MEDIA_STREAM_BASE_URL``), which lives in the platform zone. A tenant
    with a white-label assets host adds that host, in the platform zone when
    the host is a platform hostname and in the tenant's own zone otherwise.
    Read from the tenant row, so it must run while the row still exists.
    """
    from core.utils.tenant_urls import resolve_tenant_assets_domain

    schema_name = tenant.schema_name
    own_host = resolve_tenant_assets_domain(tenant).lower()
    platform = platform_zone()
    own_host_on_platform = bool(own_host) and _on_platform_zone(own_host)

    purges: list[MediaPurge] = []
    if platform:
        shared_host = (
            urlparse(settings.MEDIA_STREAM_BASE_URL).hostname or ""
        ).lower()
        hosts = {
            host
            for host in (shared_host, own_host)
            if host and _on_platform_zone(host)
        }
        if hosts:
            purges.append(
                {
                    "zone_id": platform.zone_id,
                    "prefixes": sorted(
                        _media_prefix(host, schema_name) for host in hosts
                    ),
                }
            )
    if (
        own_host
        and not own_host_on_platform
        and tenant.cloudflare_zone_id
        and tenant.cloudflare_api_token
    ):
        purges.append(
            {
                "zone_id": tenant.cloudflare_zone_id,
                "prefixes": [_media_prefix(own_host, schema_name)],
            }
        )
    return purges


def _media_zone(schema_name: str, zone_id: str) -> Zone | None:
    """The credentials for ``zone_id``, resolved when the purge runs."""
    from tenant.models import Tenant

    platform = platform_zone()
    if platform and platform.zone_id == zone_id:
        return platform
    tenant = Tenant.objects.filter(
        schema_name=schema_name, cloudflare_zone_id=zone_id
    ).first()
    if tenant and tenant.cloudflare_api_token:
        return Zone(zone_id=zone_id, api_token=tenant.cloudflare_api_token)
    return None


def purge_media(schema_name: str, purges: Sequence[MediaPurge]) -> None:
    """Purge a tenant's images from the edge, after media-stream dropped them.

    Run it after media-stream's own flush, never before: a purge that came
    first would let the edge refetch the images media-stream still holds and
    keep them for another year.

    Every zone is attempted. A zone whose credentials are gone (a destroyed
    tenant's own account) is logged and skipped: a retry cannot fix it.
    Raises ``EdgePurgeError`` once the rest are done if any zone failed, and
    the purge is idempotent, so the caller may retry.
    """
    failures: list[str] = []
    for purge in purges:
        zone = _media_zone(schema_name, purge["zone_id"])
        if zone is None:
            logger.error(
                "No Cloudflare credentials left to purge zone %s for tenant "
                "%s; purge %s by hand",
                purge["zone_id"],
                schema_name,
                ", ".join(purge["prefixes"]),
            )
            continue
        try:
            purge_prefixes(zone, purge["prefixes"])
        except EdgePurgeError as exc:
            failures.append(str(exc))
    if failures:
        raise EdgePurgeError("; ".join(failures))


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
    # The token is encrypted at rest, so SQL can only narrow on the zone
    # id; "has a token" is decided on the decrypted value.
    own = Tenant.objects.exclude(cloudflare_zone_id="").values_list(
        "cloudflare_zone_id", "cloudflare_api_token"
    )
    for zone_id, api_token in own:
        if api_token:
            zones.setdefault(
                zone_id, Zone(zone_id=zone_id, api_token=api_token)
            )
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
    return purge_tenant(bound_tenant())
