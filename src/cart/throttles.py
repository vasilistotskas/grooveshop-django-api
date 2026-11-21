"""The cart app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    GatewayAnonThrottle,
    GatewayIdentMixin,
    GatewayUserRateThrottle,
    UserIdRateThrottle,
    UserOrIpRateThrottle,
)


class CartMutationThrottle(GatewayUserRateThrottle):
    scope = "cart_mutation"


class CartAgentTokenThrottle(UserIdRateThrottle):
    """Minting hands out a credential for the shopper's cart."""

    scope = "cart_agent_token"
    fail_closed = True


class CartMutationAnonThrottle(GatewayAnonThrottle):
    scope = "cart_mutation_anon"


class CartQuoteThrottle(UserIdRateThrottle):
    """``POST /cart/quote`` for a signed-in shopper.

    A read a checkout repeats on every change and a platform polls, so
    it has a budget of its own: quotes cannot spend the cart's write
    budget (``cart_mutation``), nor writes the quotes'.

    Signed-in callers only. DRF's ``UserRateThrottle`` keys an anonymous
    request on its address, which would put every guest the agent
    gateway quotes for in one budget beside their per-cart one;
    ``CartQuoteAnonThrottle`` is the guests' budget.
    """

    scope = "cart_quote"

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        return super().get_cache_key(request, view)


class CartQuoteAnonThrottle(GatewayAnonThrottle):
    """``POST /cart/quote`` for a guest, keyed per cart when the agent
    gateway asks (it quotes on every render of an open checkout)."""

    scope = "cart_quote_anon"


class CouponApplyThrottle(GatewayIdentMixin, UserOrIpRateThrottle):
    """Tight per-caller throttle for coupon application — the endpoint is a
    brute-forceable code oracle (valid/invalid distinguishes codes).

    Behind the verified gateway secret the caller is the cart the request
    names, when that cart exists (``GatewayIdentMixin``, as
    ``GiftCardCodeThrottle``): the UCP and ACP coupon calls of every buyer
    of a platform arrive from the platform's address, so per address one
    buyer's attempts would spend everyone's. Guessing stays bounded: a
    request that names no cart, a cart that does not exist (the apply
    creates one) or no secret is keyed on the address as before, and a
    cart is minted by a cart write, which is budgeted per address.
    """

    scope = "coupon_apply"
    fail_closed = True
