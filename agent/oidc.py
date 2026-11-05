from allauth.idp.oidc.adapter import DefaultOIDCAdapter
from django.utils.translation import gettext_lazy as _

# Scopes AI agents may request when linking a shopper's account. The
# per-client granted set lives on each ``allauth.idp.oidc.Client`` row;
# this list is what the agent-facing API enforces (see agent/views.py)
# and what the consent screen can display.
SCOPE_ORDERS_READ = "orders:read"
SCOPE_LOYALTY_READ = "loyalty:read"
SCOPE_FAVOURITES_READ = "favourites:read"

# Where a store's MCP server lives on its own domain, routed to the agent
# gateway. The resource a linking client binds its token to (RFC 8707).
MCP_RESOURCE_PATH = "/mcp"


class AgentOIDCAdapter(DefaultOIDCAdapter):
    """Adds the commerce scopes to the consent screen's display copy."""

    scope_display = {
        **DefaultOIDCAdapter.scope_display,
        SCOPE_ORDERS_READ: _("View your orders and their status"),
        SCOPE_LOYALTY_READ: _("View your loyalty points and tier"),
        SCOPE_FAVOURITES_READ: _("View your favourite products"),
    }
