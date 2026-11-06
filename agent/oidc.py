from typing import Any

from allauth.idp.oidc.adapter import DefaultOIDCAdapter
from allauth.idp.oidc.internal.resources import is_resources_subset
from allauth.idp.oidc.models import Client
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Scopes AI agents may request when linking a shopper's account. The
# per-client granted set lives on each ``allauth.idp.oidc.Client`` row;
# this list is what the agent-facing API enforces (see agent/views.py)
# and what the consent screen can display.
SCOPE_ORDERS_READ = "orders:read"
SCOPE_LOYALTY_READ = "loyalty:read"
SCOPE_FAVOURITES_READ = "favourites:read"

# What a linking client may ask for. The storefront's protected-resource
# metadata (``oauth-protected-resource/mcp.get.ts``) names the same list.
LINKING_SCOPES = (
    "openid",
    "profile",
    "email",
    SCOPE_ORDERS_READ,
    SCOPE_LOYALTY_READ,
    SCOPE_FAVOURITES_READ,
)

# Where a store's MCP server lives on its own domain, routed to the agent
# gateway.
MCP_RESOURCE_PATH = "/mcp"


def store_mcp_resource() -> str:
    """The resource a linking client binds its token to (RFC 8707).

    The storefront's protected-resource metadata names this URL, on the
    store's primary domain and always over https. It is read from the
    store, not the request: a request reaches this API through other
    hosts and schemes than the one the token was bound to.
    """
    from tenant.membership import get_current_tenant

    domain = get_current_tenant().get_primary_domain().domain
    return f"https://{domain}{MCP_RESOURCE_PATH}"


class AgentOIDCAdapter(DefaultOIDCAdapter):
    """Adds the commerce scopes to the consent screen's display copy."""

    scope_display = {
        **DefaultOIDCAdapter.scope_display,
        SCOPE_ORDERS_READ: _("View your orders and their status"),
        SCOPE_LOYALTY_READ: _("View your loyalty points and tier"),
        SCOPE_FAVOURITES_READ: _("View your favourite products"),
    }

    def populate_server_metadata(
        self, data: dict[str, str | list[str]]
    ) -> None:
        """Name the commerce scopes, which allauth's own list leaves out."""
        super().populate_server_metadata(data)
        data["scopes_supported"] = list(LINKING_SCOPES)

    def validate_client_registration(
        self, *, client: Client, client_metadata: dict[str, Any], **kwargs: Any
    ) -> None:
        """Register a client that names no scopes for what linking offers.

        Registration may omit ``scope`` (RFC 7591), and allauth then stores
        ``openid`` alone, so the client's later request for the commerce
        scopes fails with ``invalid_scope`` and the shopper can never link.
        What the token finally holds is still what the shopper grants on the
        consent screen. This is allauth's only hook before the client is saved.
        """
        super().validate_client_registration(
            client=client, client_metadata=client_metadata, **kwargs
        )
        if not client_metadata.get("scope"):
            client.set_scopes(list(LINKING_SCOPES))

    def validate_resource_uris(self, *, uris: list[str], **kwargs: Any) -> None:
        """Refuse at issue a token the agent API would refuse in use."""
        store_resource = store_mcp_resource()
        for uri in uris:
            if not is_resources_subset([store_resource], [uri]):
                raise ValidationError(
                    "Resource is not this store's MCP server."
                )
