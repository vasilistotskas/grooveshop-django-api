"""Authentication and permission for the store-admin agent surface.

``/api/v1/agent/staff/*`` on a store host serves the staff MCP server
(``/mcp/admin``, through the agent gateway). It accepts one credential: an
access token from the PLATFORM's issuer (``agent/staff.py``), bound to
this store's staff MCP server, whose holder is staff of this store now.
"""

from __future__ import annotations

from typing import Any

from allauth.idp.oidc.internal.oauthlib.server import get_server
from allauth.idp.oidc.internal.oauthlib.utils import (
    extract_params,
    get_validator_context,
)
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from agent.staff import role_permission_holds, store_staff_mcp_resource
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.membership import get_current_tenant, get_membership

# The challenge realm the gateway reads a 401 by, as for the shoppers'
# ``realm="agent"``.
STAFF_AGENT_REALM = 'Bearer realm="agent-staff"'


class StaffAgentTokenAuthentication(BaseAuthentication):
    """Accept a platform-issued access token for this store's staff tools.

    - **Looked up in the public schema only.** The platform's issuer
      keeps its tokens there; a store's own issuer keeps its customers'
      tokens in the store's schema, so a customer token is never found
      here and gets the 401 any unknown token gets.
    - **Bound to this store.** The token's one resource must be this
      store's ``/mcp/admin``. A token connected to another store is
      invalid here, not short of a scope: 401, so the client connects
      again rather than retrying.
    - **Staff of this store, now.** The holder must be active, still
      ``is_staff`` (the platform's revocation flag) and hold an active
      membership in the store on the connection. Each is read on every
      call, so removing someone from the team ends their AI app's access
      at its next call, not when the token expires.

    The user comes back stamped with ``PLATFORM_IDENTITY_ATTR``, as from
    the admin login and the staff API token, so the role backend answers
    for it.
    """

    def authenticate(self, request) -> tuple[Any, Any] | None:
        with schema_context(get_public_schema_name()):
            valid, orequest = get_server().verify_request(
                *extract_params(request), scopes=[]
            )
            if not valid:
                return None
            access_token = get_validator_context().access_token
            # Read every related row while the public schema is bound:
            # the user table of the store's schema holds its customers.
            user = orequest.user
            resources = access_token.get_resources() if access_token else []
        if access_token is None or user is None:
            return None
        if resources != [store_staff_mcp_resource()]:
            raise AuthenticationFailed("Invalid target resource.")
        if not (user.is_active and user.is_staff):
            raise AuthenticationFailed("This account cannot use staff tools.")
        setattr(user, PLATFORM_IDENTITY_ATTR, True)
        if get_membership(user, get_current_tenant()) is None:
            raise AuthenticationFailed("Not a member of this store's team.")
        return user, access_token

    def authenticate_header(self, request) -> str:
        return STAFF_AGENT_REALM


def granted_scopes(request) -> set[str]:
    token = request.auth
    return set(token.get_scopes()) if token is not None else set()


class HasStaffScope(BasePermission):
    """The token holds the view's scope and the role holds its permission.

    The scope is what the person let the AI app do on the consent page;
    the permission is what the person may do in the store today. Both
    must hold, so a role lowered after consent narrows the app at once.
    A view names its scope in ``staff_scope``.
    """

    def has_permission(self, request, view) -> bool:
        scope = view.staff_scope
        user = request.user
        return (
            user is not None
            and user.is_authenticated
            and scope in granted_scopes(request)
            and role_permission_holds(user, scope)
        )
