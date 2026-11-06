from typing import Any

from allauth.idp.oidc.adapter import DefaultOIDCAdapter
from allauth.idp.oidc.internal.resources import is_resources_subset
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Scopes AI agents may request when linking a shopper's account. The
# per-client granted set lives on each ``allauth.idp.oidc.Client`` row;
# this list is what the agent-facing API enforces (see agent/views.py)
# and what the consent screen can display.
SCOPE_ORDERS_READ = "orders:read"
SCOPE_LOYALTY_READ = "loyalty:read"
SCOPE_FAVOURITES_READ = "favourites:read"

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

    def validate_resource_uris(self, *, uris: list[str], **kwargs: Any) -> None:
        """Refuse at issue a token the agent API would refuse in use."""
        store_resource = store_mcp_resource()
        for uri in uris:
            if not is_resources_subset([store_resource], [uri]):
                raise ValidationError(
                    "Resource is not this store's MCP server."
                )
