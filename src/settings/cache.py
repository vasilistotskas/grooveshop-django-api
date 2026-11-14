"""Redis and the cache: Django's, the storefront's and the edge's."""

import tomllib
from os import getenv

from settings.env import BASE_DIR, SYSTEM_ENV

REDIS_HOST = getenv("REDIS_HOST", "localhost")
REDIS_PORT = getenv("REDIS_PORT", "6379")
REDIS_PASSWORD = getenv("REDIS_PASSWORD", "")
REDIS_URL = (
    f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0"
    if REDIS_PASSWORD
    else f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
)

DEFAULT_CACHE_KEY_PREFIX = getenv("DEFAULT_CACHE_KEY_PREFIX", "default")
DEFAULT_CACHE_VERSION = int(getenv("DEFAULT_CACHE_VERSION", "1"))
DEFAULT_CACHE_TTL = int(getenv("DEFAULT_CACHE_TTL", "7200"))
DISABLE_CACHE = getenv("DISABLE_CACHE", "False").lower() == "true"

# The running release, read from the pyproject.toml every image ships
# (semantic-release bumps it in the release commit the image is built
# from). ``cache_methods`` namespaces the response cache with it, so a
# new release never serves a body an older one cached — the same
# build-scoped design as the storefront's ``cache:<buildId>`` keys.
with (BASE_DIR / "pyproject.toml").open("rb") as _pyproject:
    RELEASE_VERSION: str = tomllib.load(_pyproject)["project"]["version"]

CACHES = {
    "default": {
        # CustomCache = RedisCache + schema-scoped SCAN/UNLINK helpers
        # (keys/delete_raw_keys/clear_by_prefixes). Registering it as
        # THE default backend means the admin cache-purge service and
        # the clear_cache command operate on the same tenant-keyed
        # connection as every cache.get/set — no parallel raw client
        # that bypasses make_tenant_key.
        "BACKEND": "core.caches.CustomCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": DEFAULT_CACHE_KEY_PREFIX,
        "VERSION": DEFAULT_CACHE_VERSION,
        "TIMEOUT": DEFAULT_CACHE_TTL,
        "KEY_FUNCTION": "tenant.cache.make_tenant_key",
    },
}

CACHE_CLEAR_PREFIXES: list[str] = [
    f"{DEFAULT_CACHE_KEY_PREFIX}:{DEFAULT_CACHE_VERSION}:",  # Django: "redis:1:"
    "cache:",  # Nuxt SSR cache
]

# Internal Nuxt URL used by ``core.cache`` to invalidate Nitro SSR
# caches synchronously from the Django admin. In Kubernetes this is
# the cluster-internal Service DNS (e.g. ``http://frontend-nuxt:3000``)
# so Cloudflare/Traefik are bypassed and the call is fast.
NUXT_INTERNAL_BASE_URL = getenv("NUXT_INTERNAL_BASE_URL", "")
NUXT_CACHE_PURGE_TOKEN = getenv("NUXT_CACHE_PURGE_TOKEN", "")
# How long each storefront pod keeps a store's resolve payload in memory
# (``TENANT_CACHE_TTL`` in the storefront's server/utils/tenant.ts). A
# purge that a plan-flag change triggers waits this long, or a page could
# re-render from the old copy and be cached with the old flag again.
STOREFRONT_TENANT_CACHE_SECONDS = int(
    getenv("STOREFRONT_TENANT_CACHE_SECONDS", "300")
)

# The platform's own Cloudflare zone, which caches the storefront pages of
# every store on a platform hostname (``core.cache.edge``). A store on its
# own domain in its own Cloudflare account carries its credentials on the
# Tenant row instead. The token needs only Zone → Cache Purge → Purge.
CLOUDFLARE_PLATFORM_ZONE_NAME = getenv("CLOUDFLARE_PLATFORM_ZONE_NAME", "")
CLOUDFLARE_PLATFORM_ZONE_ID = getenv("CLOUDFLARE_PLATFORM_ZONE_ID", "")
CLOUDFLARE_PLATFORM_API_TOKEN = getenv("CLOUDFLARE_PLATFORM_API_TOKEN", "")

if SYSTEM_ENV == "ci":
    CACHES = {
        "default": {
            "BACKEND": "core.caches.CustomCache",
            "LOCATION": "redis://127.0.0.1:6379/0",
            "KEY_PREFIX": "redis",
            # KEY_FUNCTION must match production — dropping it here
            # silently disabled tenant cache isolation in CI, so the
            # suite never exercised the keys it ships with.
            "KEY_FUNCTION": "tenant.cache.make_tenant_key",
        },
    }
