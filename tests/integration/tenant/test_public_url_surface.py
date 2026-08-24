"""The platform control-plane host must not mount the storefront API.

``PUBLIC_SCHEMA_URLCONF = "tenant.urls_public"`` is served only on the
PUBLIC schema — i.e. the platform host ``platform.grooveshop.space``.
It composes ``core.urls.public_shared_urlpatterns`` (shared infra +
control-plane data) plus the platform-only admin/staff endpoints, and
deliberately OMITS the merchant storefront/commerce API. That keeps a
store's catalogue, orders, customers, cart, loyalty and agent resources
structurally absent from the control plane — defence in depth behind the
platform auth wall (audit finding M1, 2026-08).

The tenant/storefront host (``ROOT_URLCONF = "core.urls"``) is
unchanged: it still serves the whole surface. Both facts are pinned here
so a future URL edit cannot silently re-expose store data on the platform
host, nor drop it from a tenant host.

NOTE: ``tests/conftest.py`` deletes ``settings.PUBLIC_SCHEMA_URLCONF`` so
the suite runs against ROOT. These tests therefore reference the two URL
confs by name via ``resolve(..., urlconf=...)`` rather than relying on
schema-based routing.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import Resolver404, resolve

PUBLIC = "tenant.urls_public"
ROOT = "core.urls"

# One concrete, parameter-free path per storefront/commerce app group.
# These MUST resolve on the tenant host and MUST NOT on the platform host.
STOREFRONT_PATHS = [
    "/api/v1/product",
    "/api/v1/order",
    "/api/v1/cart/list",
    "/api/v1/user/account",
    "/api/v1/blog/post",
    "/api/v1/tag",
    "/api/v1/loyalty/summary",
    "/api/v1/search/blog/post",
    "/api/v1/pay_way",
    "/api/v1/shipping/options",
    "/api/v1/shipping/acs/stations",
    "/api/v1/shipping/boxnow/lockers",
    "/api/v1/notification/ids",
    "/api/v1/contact",
    "/api/v1/page-config/navigation",
    "/api/v1/agent/me",
]

# Shared infra + control-plane data. MUST resolve on BOTH hosts.
SHARED_PATHS = [
    "/api/v1/health",
    "/api/v1/settings",
    "/api/v1/schema",
    "/api/v1/country",
    "/api/v1/region",
    "/api/v1/tenant/resolve",
]

# Platform control-plane only. MUST resolve on the platform host.
PLATFORM_ONLY_PATHS = [
    "/admin/",
    "/api/v1/platform/auth/login",
    "/api/v1/tenant/admin/",
]


def _resolves(path: str, urlconf: str) -> bool:
    try:
        resolve(path, urlconf=urlconf)
        return True
    except Resolver404:
        return False


class TestPlatformHostHidesStorefrontApi(TestCase):
    def test_storefront_api_is_absent_from_the_platform_host(self):
        leaked = [p for p in STOREFRONT_PATHS if _resolves(p, PUBLIC)]
        assert not leaked, (
            f"storefront endpoints reachable on the platform host: {leaked}"
        )

    def test_storefront_api_still_served_on_tenant_hosts(self):
        """Guard: the fix must not remove these from ROOT_URLCONF."""
        missing = [p for p in STOREFRONT_PATHS if not _resolves(p, ROOT)]
        assert not missing, (
            f"storefront endpoints missing from the tenant host: {missing}"
        )


class TestSharedSurfaceServedEverywhere(TestCase):
    def test_shared_endpoints_resolve_on_both_hosts(self):
        for path in SHARED_PATHS:
            assert _resolves(path, PUBLIC), f"{path} missing on platform host"
            assert _resolves(path, ROOT), f"{path} missing on tenant host"


class TestPlatformControlPlaneSurface(TestCase):
    def test_platform_only_endpoints_resolve_on_the_platform_host(self):
        for path in PLATFORM_ONLY_PATHS:
            assert _resolves(path, PUBLIC), f"{path} missing on platform host"

    def test_platform_admin_is_the_control_plane_site(self):
        match = resolve("/admin/", urlconf=PUBLIC)
        assert match.namespace == "platform_admin"
