"""Store-admin tools over MCP: who may connect, and what each scope opens.

A store's owner and staff connect their AI app to the store's staff MCP
server (``https://<store>/mcp/admin``, served by the agent gateway) and
ask it about orders, sales, stock, customers, reviews and messages. The
design is ``grooveshop-docs/src/dev/flows/staff-mcp.md``.

Staff are PLATFORM identities (public schema, a role per store), not the
store's customers, so their tokens come from a second issuer:
``allauth.idp.oidc`` is in SHARED_APPS as well as TENANT_APPS, and on the
platform host (public schema) it issues tokens to platform identities.
A store's own issuer keeps issuing to its customers from its schema. A
customer token therefore cannot be found where staff tokens are looked
up, and the other way round.

Every tool needs two things: its scope, granted on the consent page, and
the Django permission behind it, which the person's role in the store
must hold at the time of the call. The role is read through
``TenantRolePermissionBackend`` directly, never ``user.has_perm``, whose
superuser short-circuit would hand a platform superuser's AI app every
permission in every store: an AI-app token carries the store role and
nothing more.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from django.db import connection
from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise
from django_tenants.utils import get_public_schema_name

# Where a store's staff MCP server lives on its own domain, routed to the
# agent gateway next to the shoppers' ``/mcp``.
STAFF_MCP_RESOURCE_PATH = "/mcp/admin"

SCOPE_STAFF_ORDERS_READ = "admin:orders:read"
SCOPE_STAFF_SALES_READ = "admin:sales:read"
SCOPE_STAFF_PROFIT_READ = "admin:profit:read"
SCOPE_STAFF_CATALOG_READ = "admin:catalog:read"
SCOPE_STAFF_CUSTOMERS_READ = "admin:customers:read"
SCOPE_STAFF_REVIEWS_READ = "admin:reviews:read"
SCOPE_STAFF_MESSAGES_READ = "admin:messages:read"

# The Django permission each scope stands for. A tool answers only when
# the token holds the scope AND the caller's role holds the permission.
STAFF_SCOPE_PERMISSIONS: dict[str, str] = {
    SCOPE_STAFF_ORDERS_READ: "order.view_order",
    SCOPE_STAFF_SALES_READ: "order.view_turnover_report",
    SCOPE_STAFF_PROFIT_READ: "order.view_profit_report",
    SCOPE_STAFF_CATALOG_READ: "product.view_product",
    SCOPE_STAFF_CUSTOMERS_READ: "user.view_useraccount",
    SCOPE_STAFF_REVIEWS_READ: "product.view_productreview",
    SCOPE_STAFF_MESSAGES_READ: "contact.view_contact",
}

# Asked for by Claude's hosted apps on every authorization. allauth
# issues refresh tokens whatever the scope, so it opens nothing; a staff
# client may hold it so the request is not refused.
SCOPE_OFFLINE_ACCESS = "offline_access"

# What a client registered on the platform's issuer may ask for.
STAFF_CLIENT_SCOPES: tuple[str, ...] = (
    *STAFF_SCOPE_PERMISSIONS,
    SCOPE_OFFLINE_ACCESS,
)

STAFF_SCOPE_DISPLAY: dict[str, StrOrPromise] = {
    SCOPE_STAFF_ORDERS_READ: _("View the store's orders"),
    SCOPE_STAFF_SALES_READ: _("View the store's sales and turnover"),
    SCOPE_STAFF_PROFIT_READ: _("View the store's profit and costs"),
    SCOPE_STAFF_CATALOG_READ: _("View products, prices and stock"),
    SCOPE_STAFF_CUSTOMERS_READ: _("View customers"),
    SCOPE_STAFF_REVIEWS_READ: _("View product reviews"),
    SCOPE_STAFF_MESSAGES_READ: _("View contact messages"),
    SCOPE_OFFLINE_ACCESS: _("Stay connected until you disconnect"),
}

# Grant types a client of the platform's issuer may register. The device
# flow is left out: its verification page signs people in through
# allauth's account login, which knows neither the staff lockouts nor the
# superuser's second factor.
STAFF_CLIENT_GRANT_TYPES: frozenset[str] = frozenset(
    {"authorization_code", "refresh_token"}
)


def on_platform_issuer() -> bool:
    """True while the request is served by the platform's (staff) issuer."""
    return connection.schema_name == get_public_schema_name()


def staff_mcp_resource(domain: str) -> str:
    """The resource a staff token binds to for the store at *domain*."""
    return f"https://{domain}{STAFF_MCP_RESOURCE_PATH}"


def store_staff_mcp_resource() -> str:
    """This store's staff MCP server, on its primary domain.

    Read from the store, not the request, as for the shoppers' resource
    (``agent.oidc.store_mcp_resource``).
    """
    from tenant.membership import get_current_tenant

    tenant = get_current_tenant()
    if tenant is None:
        raise RuntimeError("No store is bound to this request.")
    return staff_mcp_resource(tenant.get_primary_domain().domain)


def store_for_staff_resource(uri: str) -> Any | None:
    """The active store whose staff MCP server *uri* names, or None.

    Only the exact form ``https://<primary domain>/mcp/admin`` names a
    store: no other scheme, port, path, query or a secondary domain, so
    every token for one store carries the same resource and the store
    host can compare it with a plain equality.
    """
    from tenant.models import TenantDomain

    parts = urlsplit(uri)
    if (
        parts.scheme != "https"
        or parts.path != STAFF_MCP_RESOURCE_PATH
        or parts.query
        or parts.fragment
        or parts.port is not None
        or parts.username
        or not parts.hostname
    ):
        return None
    domain = (
        TenantDomain.objects.filter(
            domain=parts.hostname,
            is_primary=True,
            tenant__is_active=True,
        )
        .select_related("tenant")
        .first()
    )
    if domain is None or domain.tenant.schema_name == get_public_schema_name():
        return None
    return domain.tenant


def role_permission_holds(user: Any, scope: str) -> bool:
    """Whether *user*'s role in the store on the connection opens *scope*.

    The role alone decides: ``TenantRolePermissionBackend`` answers from
    the membership, and needs the user to carry the platform-identity
    stamp, which only the staff authenticators and the staff session
    set.
    """
    from tenant.auth_backends import TenantRolePermissionBackend

    permission = STAFF_SCOPE_PERMISSIONS.get(scope)
    if permission is None:
        return False
    return TenantRolePermissionBackend().has_perm(user, permission)


def scopes_for_role(user: Any) -> list[str]:
    """The staff scopes *user*'s role in the store on the connection opens."""
    return [
        scope
        for scope in STAFF_SCOPE_PERMISSIONS
        if role_permission_holds(user, scope)
    ]
