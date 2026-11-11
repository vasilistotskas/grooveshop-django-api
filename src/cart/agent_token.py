"""The cart-scoped token the shopping assistant acts on a cart with.

The assistant is served by the agent gateway, which calls this API
anonymously, so a signed-in shopper's cart cannot be addressed by its
``X-Cart-Id`` (an anonymous caller only ever reaches guest carts). The
storefront mints a token for the shopper's OWN cart with
``POST /api/v1/cart/agent-token`` and the gateway sends it back as
``X-Cart-Token``.

It is deliberately not an authentication class. ``CartService`` resolves
it, and only the cart views that list their actions as token-capable ask
it to: every other endpoint, orders and profile included, still sees an
anonymous request, and a cart action outside the allowlist refuses the
header outright.

Storage: the tenant cache (the default cache keys by schema, so a token
minted on one store is a miss on another), under the SHA-256 of the
token, never the token. The value names the cart, its owner and the
digest of the knox login that minted it; every use re-checks that login
is still live, so logging out revokes the token.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from rest_framework import status
from rest_framework.exceptions import APIException

from core.api.tokens import is_auth_token_live

if TYPE_CHECKING:
    from django.http import HttpRequest
    from rest_framework.request import Request

    from cart.models import Cart

CART_TOKEN_CACHE_PREFIX = "cart:agent-token:"

CART_TOKEN_PARAMETER = OpenApiParameter(
    name="X-Cart-Token",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    description=(
        "Cart token from ``POST /api/v1/cart/agent-token``: acts on the "
        "signed-in shopper's own cart, for the shopping assistant. When "
        "sent, ``X-Cart-Id`` is ignored. An unknown, expired or revoked "
        "token is refused with 401, never answered with another cart; an "
        "endpoint that does not accept it refuses it with 403."
    ),
    required=False,
)


class CartTokenInvalid(APIException):
    """Unknown, expired or revoked token: 401, never a fresh guest cart.

    Quietly falling back to a new guest cart is the failure this token
    exists to end: the assistant would report an add the shopper never
    gets. The caller mints a new token and retries.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = _("The cart token is invalid or has expired.")
    default_code = "cart_token_invalid"


class CartTokenNotPermitted(APIException):
    """The cart token was sent to something it may not act on: 403."""

    status_code = status.HTTP_403_FORBIDDEN
    default_detail = _("The cart token is not accepted by this endpoint.")
    default_code = "cart_token_not_permitted"


@dataclass(frozen=True)
class CartTokenGrant:
    cart_id: int
    user_id: int
    knox_digest: str


def hash_cart_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _cache_key(token: str) -> str:
    return f"{CART_TOKEN_CACHE_PREFIX}{hash_cart_token(token)}"


def cart_token_from_request(request: Request | HttpRequest) -> str | None:
    """The ``X-Cart-Token`` the caller sent, if any."""
    if hasattr(request, "META"):
        raw = request.META.get("HTTP_X_CART_TOKEN")
    else:
        raw = request.headers.get("X-Cart-Token")
    return raw or None


def mint_cart_token(cart: Cart, auth_token) -> str:
    """Mint a token for ``cart``, bound to the knox login ``auth_token``."""
    token = secrets.token_urlsafe(32)
    grant = CartTokenGrant(
        cart_id=cart.pk,
        user_id=auth_token.user_id,
        knox_digest=auth_token.digest,
    )
    cache.set(
        _cache_key(token),
        asdict(grant),
        settings.CART_AGENT_TOKEN_TTL_SECONDS,
    )
    return token


def resolve_cart_token(token: str) -> Cart:
    """The cart ``token`` authorises, or ``CartTokenInvalid``.

    Re-checked on every use: the minting login is still live, its owner
    is active, and the cart still belongs to that owner.
    """
    from cart.models import Cart

    stored = cache.get(_cache_key(token))
    if not stored:
        raise CartTokenInvalid
    grant = CartTokenGrant(**stored)
    if not is_auth_token_live(grant.user_id, grant.knox_digest):
        raise CartTokenInvalid
    cart = (
        Cart.objects.select_related("user")
        .filter(pk=grant.cart_id, user_id=grant.user_id, user__is_active=True)
        .first()
    )
    if cart is None:
        raise CartTokenInvalid
    return cart
