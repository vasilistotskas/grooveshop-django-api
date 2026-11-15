"""A store without customer accounts (``CUSTOMER_ACCOUNTS_ENABLED``).

The switch is a store feature, ``customer_accounts`` in
``tenant.features.STORE_FEATURES``. Off, it takes three things away on the
store's hosts, each where every path to it passes:

- **The identity routes.** allauth's headless API and pages (sign-up,
  sign-in by password, code, passkey or a social provider, password reset,
  email verification, MFA, sessions), the social providers' callbacks and
  the store's OIDC provider with its metadata answer a bare 404:
  ``tenant.middleware.CustomerAccountsMiddleware``, over the views
  ``core.urls.CUSTOMER_IDENTITY_VIEWS`` collects from the URL groups that
  mount them. The agent routes acting for a linked shopper refuse through
  ``agent.views.AgentAPIView``.
- **A sign-in a shopper already holds.** Tokens are left to expire, never
  revoked (owner, 2026-10-14), so every authenticator that can return a
  customer stops accepting one: the Knox token
  (``core.api.tokens.BoundedTokenAuthentication``), the session
  (``StoreSessionAuthentication`` below), the OIDC access token
  (``agent.views.AgentTokenAuthentication``), the cart agent token
  (``core.api.tokens.is_auth_token_live``) and the WebSocket ticket
  (``core.middleware.channels``). Each answers as if no credential had
  been sent, so an account endpoint refuses the request as it refuses a
  guest's, and a page a guest may use (the catalogue, checkout) serves it
  as a guest's. A store team member is a platform identity, never a
  customer, and is not affected.
- **What needs an account.** Every feature that does lists the switch
  in its row, so it reads as off and its routes refuse.

The platform host is not a store and is never gated. ``docs/store-features.md``,
"Customer accounts".
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.authentication import SessionScheme
from rest_framework.authentication import SessionAuthentication

from tenant.features import feature_on
from tenant.membership import get_current_tenant, is_platform_identity

FEATURE = "customer_accounts"


def customer_accounts_on() -> bool:
    """Whether the store serving this request signs its shoppers in.

    Always on the platform host (the public schema), whose sign-in is the
    platform's own, whatever a setting row there says.
    """
    return get_current_tenant() is None or feature_on(FEATURE)


def refuses_customer(user: Any) -> bool:
    """Whether ``user`` is a shopper of a store whose accounts are off.

    A platform identity (the store's team, a platform operator) is never a
    customer, so its sign-in stands.
    """
    return not is_platform_identity(user) and not customer_accounts_on()


class StoreSessionAuthentication(SessionAuthentication):
    """DRF's session authentication, minus a customer's session on a store
    with accounts off.

    Checked before DRF's CSRF enforcement: a session that is no sign-in
    has no request to protect, and the request goes on as a guest's.
    """

    def authenticate(self, request):
        user = getattr(request._request, "user", None)
        if getattr(user, "is_authenticated", False) and refuses_customer(user):
            return None
        return super().authenticate(request)


class StoreSessionScheme(SessionScheme):
    """``StoreSessionAuthentication`` in the OpenAPI schema: the session
    cookie it is (``cookieAuth``)."""

    target_class = "tenant.customer_accounts.StoreSessionAuthentication"
