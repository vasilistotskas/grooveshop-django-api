import hmac

from django.conf import settings
from rest_framework.throttling import (
    AnonRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
)


def _gateway_cart_ident(request) -> str | None:
    """Cart UUID to throttle on when the request is from the agent gateway.

    The gateway authenticates itself with the ``X-Internal-Gateway``
    shared secret (its ``INTERNAL_EVENTS_SECRET``). Returns ``None`` —
    meaning "throttle normally" — unless the secret is configured,
    matches, and the request carries a cart UUID.
    """
    secret = settings.AGENT_GATEWAY_INTERNAL_SECRET
    provided = request.headers.get("X-Internal-Gateway", "")
    if not secret or not provided:
        return None
    if not hmac.compare_digest(provided, secret):
        return None
    return request.headers.get("X-Cart-Id") or None


class UserOrIpRateThrottle(SimpleRateThrottle):
    """A scoped budget that applies to every caller, signed in or not.

    ``AnonRateThrottle.get_cache_key`` returns ``None`` for an
    authenticated request — that is its documented job, and it is the
    right base for the ``*AnonThrottle`` classes below, each of which
    has a ``UserRateThrottle`` sibling covering the other half.

    It is the wrong base for a budget that is meant to bound an
    *endpoint*. A scoped throttle built on it stops existing the moment
    the caller signs in, so "this endpoint must not be enumerable" and
    "a request amplifier against VIES" were true only of visitors. Five
    of these endpoints had no other throttle at all, which made logging
    in the way to remove the limit.

    Keyed by user id when authenticated and by ``get_ident`` (the
    NUM_PROXIES-aware client IP) otherwise, so one signed-in caller
    cannot spend another's budget and a shared office IP no longer puts
    every colleague in one bucket.
    """

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        ident = (
            f"user:{user.pk}"
            if user is not None and user.is_authenticated
            else self.get_ident(request)
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ContactCreateThrottle(UserOrIpRateThrottle):
    scope = "contact"


class FeedbackCreateThrottle(UserOrIpRateThrottle):
    scope = "feedback"


class PaymentAttemptThrottle(UserRateThrottle):
    scope = "payment"


class PaymentAttemptAnonThrottle(AnonRateThrottle):
    scope = "payment_anon"


class OrderCreateThrottle(UserRateThrottle):
    scope = "order_create"


class OrderCreateAnonThrottle(AnonRateThrottle):
    """Anonymous checkout is a stock- and money-moving endpoint.

    Creating an order reserves or decrements stock, can mint a courier
    voucher and can open a provider payment session, all before anyone
    has authenticated. The global anon budget is a day-scale ceiling and
    does not bound a burst.
    """

    scope = "order_create_anon"


class CartMutationThrottle(UserRateThrottle):
    scope = "cart_mutation"


class CartMutationAnonThrottle(AnonRateThrottle):
    scope = "cart_mutation_anon"

    def get_cache_key(self, request, view):
        # All AI-agent traffic egresses from agent-gateway pods, so the
        # default REMOTE_ADDR key would put every agent in one shared
        # 30/min bucket. Authenticated gateway requests are keyed on the
        # cart UUID instead; everyone else keeps the per-IP key.
        ident = _gateway_cart_ident(request)
        if ident:
            return self.cache_format % {
                "scope": self.scope,
                "ident": f"gw:{ident}",
            }
        return super().get_cache_key(request, view)


class CouponApplyThrottle(UserOrIpRateThrottle):
    """Tight per-caller throttle for coupon application — the endpoint is a
    brute-forceable code oracle (valid/invalid distinguishes codes)."""

    scope = "coupon_apply"


class GiftCardCheckThrottle(UserOrIpRateThrottle):
    """Tight per-caller throttle for the gift-card balance check — the code
    IS the bearer secret, so this endpoint must not be enumerable."""

    scope = "gift_card_check"


class B2BProfileSubmitThrottle(UserOrIpRateThrottle):
    """Tight per-caller throttle for business-profile submits — each one can
    trigger an outbound VIES HTTP check (5s timeout), so an unthrottled
    endpoint is a request amplifier against both our workers and VIES."""

    scope = "b2b_profile_submit"


class SearchThrottle(UserOrIpRateThrottle):
    scope = "search"


class SearchClickThrottle(UserOrIpRateThrottle):
    scope = "search_click"


class ViewCountThrottle(UserOrIpRateThrottle):
    """Tight per-caller throttle for the product view-count increment endpoint."""

    scope = "view_count"


class VivaReturnThrottle(UserOrIpRateThrottle):
    """Per-caller throttle for the anonymous Viva hosted-checkout return
    resolver. The global anon limit (100k/day) is far too loose for an
    AllowAny lookup that echoes order id/uuid/status — cap it tightly."""

    scope = "viva_return"


class AcsAddressValidationThrottle(UserOrIpRateThrottle):
    """Per-caller throttle for the public ACS address-validation proxy, which
    forwards to the rate-limited ACS partner API (G0016)."""

    scope = "acs_address"


class BoxNowNearestThrottle(UserOrIpRateThrottle):
    """Per-caller throttle for the public BoxNow nearest-locker proxy, which
    forwards synchronously to the BoxNow partner API (G0059)."""

    scope = "boxnow_nearest"
