"""Throttles, and what each one does while Redis is unreachable.

Every throttle keeps its request history in Redis. The default cache
fails open (``core.caches.CustomCache``): during an outage a read is a
miss and a write does nothing, so a throttle reading through it would
see no history and allow everything. For the day-scale ``anon`` /
``user`` browsing budgets that is right — a Redis blip must not take the
storefront API down — and DRF's own ``AnonRateThrottle`` /
``UserRateThrottle``, used directly by views and as
``DEFAULT_THROTTLE_CLASSES``, behave exactly that way.

It is wrong for the scopes that guard something. So every throttle
defined here uses ``ResilientThrottleMixin``: its keys live under
``core.caches.STRICT_NAMESPACE``, which the cache never fails open on,
and the class decides what an outage means with ONE attribute,
``fail_closed``:

- ``fail_closed = True`` — DENY (DRF answers 429 with ``Retry-After``)
  and log at ERROR, once per burst. The rule: the endpoint is a secret
  or existence oracle (coupon, gift card, order lookup, staff login),
  moves money or stock (order create, payment), makes us send email to
  an address the caller chooses (newsletter), stores the caller's
  uploads (contact attachments), or spends a third party's budget on the
  caller's behalf (VIES, the ACS and BoxNow partner APIs).
- ``fail_closed = False`` — ALLOW and log at WARNING, once per burst:
  every other scope (browsing, search, cart edits, analytics events,
  messages to the store's own inbox).
"""

import hmac
import logging
from typing import ClassVar

from django.conf import settings
from rest_framework.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
)

from core.caches import REDIS_UNAVAILABLE, STRICT_NAMESPACE, BurstLogger
from core.client_ip import trusted_client_ip

logger = logging.getLogger(__name__)

_denied_while_unavailable = BurstLogger(
    "Throttle %s DENIED requests: Redis unavailable, and this scope fails "
    "closed: %s (%s similar denial(s) suppressed in the last %ss)",
    level=logging.ERROR,
    target=logger,
)
_allowed_while_unavailable = BurstLogger(
    "Throttle %s ALLOWED requests unthrottled: Redis unavailable, and this "
    "scope fails open: %s (%s similar event(s) suppressed in the last %ss)",
    target=logger,
)


class ResilientThrottleMixin:
    """Decide, per throttle class, what a Redis outage means.

    Mix in ahead of a ``SimpleRateThrottle`` subclass. The key format
    moves under ``STRICT_NAMESPACE``, so ``CustomCache`` re-raises the
    connection error instead of reporting an empty history, and
    ``allow_request`` turns it into the class's policy — see the module
    docstring for which scopes deny.
    """

    fail_closed: ClassVar[bool] = False
    cache_format = STRICT_NAMESPACE + "throttle_%(scope)s_%(ident)s"

    def allow_request(self, request, view) -> bool:
        try:
            return super().allow_request(request, view)  # type: ignore[misc]
        except REDIS_UNAVAILABLE as exc:
            # ``wait()`` reads these to compute ``Retry-After``; with no
            # history it suggests a fraction of the window.
            self.history = []
            self.now = self.timer()  # type: ignore[attr-defined]
            scope = getattr(self, "scope", None)
            if self.fail_closed:
                _denied_while_unavailable.report(scope, exc)
                return False
            _allowed_while_unavailable.report(scope, exc)
            return True


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
    # Bytes, not str: `compare_digest` raises TypeError on non-ASCII
    # str, and header bytes reach here latin-1-decoded, so a single
    # high byte would 500 this request instead of throttling it.
    if not hmac.compare_digest(
        provided.encode("utf-8", "surrogateescape"),
        secret.encode("utf-8", "surrogateescape"),
    ):
        return None
    return request.headers.get("X-Cart-Id") or None


class UserOrIpRateThrottle(ResilientThrottleMixin, SimpleRateThrottle):
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

    Keyed by user id when authenticated and by the real client IP
    otherwise, so one signed-in caller cannot spend another's budget and
    a shared office IP no longer puts every colleague in one bucket.

    For anonymous callers the IP comes from ``core.client_ip``, NOT from
    ``get_ident``. Behind k3s ServiceLB every inbound connection is SNATd
    to the node's Flannel gateway before Traefik sees it, so the
    NUM_PROXIES-aware rightmost X-Forwarded-For hop is an internal
    ``10.42.x.x`` address — proven in production 2026-09-16. Keying on it
    puts EVERY anonymous visitor in one bucket, which turns a per-caller
    budget into a store-wide one: at ``order_create_anon`` 10/minute, a
    single client could lock all guests out of checkout. ``get_ident``
    remains the fallback for requests whose provenance cannot be proven,
    because it is coarse but cannot be forged.
    """

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            ident = f"user:{user.pk}"
        else:
            ident = trusted_client_ip(request) or self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ContactCreateThrottle(UserOrIpRateThrottle):
    scope = "contact"


class FeedbackCreateThrottle(UserOrIpRateThrottle):
    scope = "feedback"


class NewsletterSubscribeThrottle(UserOrIpRateThrottle):
    """Per-caller budget for the anonymous newsletter form, which sends a
    confirmation email to whatever address it is given."""

    scope = "newsletter_subscribe"
    fail_closed = True


class ContactAttachmentThrottle(UserOrIpRateThrottle):
    """Tight per-caller budget for the anonymous attachment upload.

    Its own scope rather than the contact one: a visitor legitimately
    uploads several files before submitting ONE enquiry, so sharing the
    form's budget would make attaching three drawings spend the
    allowance for sending the message. Kept low in absolute terms
    because each request can leave tens of megabytes on the pod's
    ephemeral disk until the enquiry claims it or the reaper takes it.
    """

    scope = "contact_attachment"
    fail_closed = True


class PaymentAttemptThrottle(ResilientThrottleMixin, UserRateThrottle):
    scope = "payment"
    fail_closed = True


class PaymentAttemptAnonThrottle(ResilientThrottleMixin, AnonRateThrottle):
    scope = "payment_anon"
    fail_closed = True


class OrderCreateThrottle(ResilientThrottleMixin, UserRateThrottle):
    scope = "order_create"
    fail_closed = True


class OrderCreateAnonThrottle(ResilientThrottleMixin, AnonRateThrottle):
    """Anonymous checkout is a stock- and money-moving endpoint.

    Creating an order reserves or decrements stock, can mint a courier
    voucher and can open a provider payment session, all before anyone
    has authenticated. The global anon budget is a day-scale ceiling and
    does not bound a burst.
    """

    scope = "order_create_anon"
    fail_closed = True


class CartMutationThrottle(ResilientThrottleMixin, UserRateThrottle):
    scope = "cart_mutation"


class CartMutationAnonThrottle(ResilientThrottleMixin, AnonRateThrottle):
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
    fail_closed = True


class GiftCardCheckThrottle(UserOrIpRateThrottle):
    """Tight per-caller throttle for the gift-card balance check — the code
    IS the bearer secret, so this endpoint must not be enumerable."""

    scope = "gift_card_check"
    fail_closed = True


class B2BProfileSubmitThrottle(UserOrIpRateThrottle):
    """Tight per-caller throttle for business-profile submits — each one can
    trigger an outbound VIES HTTP check (5s timeout), so an unthrottled
    endpoint is a request amplifier against both our workers and VIES."""

    scope = "b2b_profile_submit"
    fail_closed = True


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
    fail_closed = True


class AcsAddressValidationThrottle(UserOrIpRateThrottle):
    """Per-caller throttle for the public ACS address-validation proxy, which
    forwards to the rate-limited ACS partner API (G0016)."""

    scope = "acs_address"
    fail_closed = True


class BoxNowNearestThrottle(UserOrIpRateThrottle):
    """Per-caller throttle for the public BoxNow nearest-locker proxy, which
    forwards synchronously to the BoxNow partner API (G0059)."""

    scope = "boxnow_nearest"
    fail_closed = True


class RecommendationEventThrottle(UserOrIpRateThrottle):
    """Budget for suggestion-strip impression/click events. Its own
    scope so a scripted client cannot starve the search allowance."""

    scope = "recommendation_event"


class StaffLoginThrottle(ResilientThrottleMixin, ScopedRateThrottle):
    """The platform staff token endpoint — a credential-guessing surface
    (``tenant/staff_api.py``). Scoped by the view's ``throttle_scope``."""

    fail_closed = True
