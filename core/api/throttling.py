import hmac

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


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


class ContactCreateThrottle(AnonRateThrottle):
    scope = "contact"


class FeedbackCreateThrottle(AnonRateThrottle):
    scope = "feedback"


class PaymentAttemptThrottle(UserRateThrottle):
    scope = "payment"


class PaymentAttemptAnonThrottle(AnonRateThrottle):
    scope = "payment_anon"


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


class CouponApplyThrottle(AnonRateThrottle):
    """Tight per-IP throttle for coupon application — the endpoint is a
    brute-forceable code oracle (valid/invalid distinguishes codes)."""

    scope = "coupon_apply"


class GiftCardCheckThrottle(AnonRateThrottle):
    """Tight per-IP throttle for the gift-card balance check — the code
    IS the bearer secret, so this endpoint must not be enumerable."""

    scope = "gift_card_check"


class SearchThrottle(AnonRateThrottle):
    scope = "search"


class SearchClickThrottle(AnonRateThrottle):
    scope = "search_click"


class ViewCountThrottle(AnonRateThrottle):
    """Tight per-IP throttle for the product view-count increment endpoint."""

    scope = "view_count"


class VivaReturnThrottle(AnonRateThrottle):
    """Per-IP throttle for the anonymous Viva hosted-checkout return
    resolver. The global anon limit (100k/day) is far too loose for an
    AllowAny lookup that echoes order id/uuid/status — cap it tightly."""

    scope = "viva_return"


class AcsAddressValidationThrottle(AnonRateThrottle):
    """Per-IP throttle for the public ACS address-validation proxy, which
    forwards to the rate-limited ACS partner API (G0016)."""

    scope = "acs_address"


class BoxNowNearestThrottle(AnonRateThrottle):
    """Per-IP throttle for the public BoxNow nearest-locker proxy, which
    forwards synchronously to the BoxNow partner API (G0059)."""

    scope = "boxnow_nearest"
