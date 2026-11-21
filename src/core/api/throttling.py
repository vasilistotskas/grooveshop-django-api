"""Throttles, and what each one does while Redis is unreachable.

Every throttle keeps its request history in Redis. The default cache
fails open (``core.caches.CustomCache``): during an outage a read is a
miss and a write does nothing, so a throttle reading through it would
see no history and allow everything. For the day-scale ``anon`` /
``user`` browsing budgets that is right — a Redis blip must not take the
storefront API down.

It is wrong for the scopes that guard something. So every throttle
uses ``ResilientThrottleMixin``: its keys live under
``core.caches.STRICT_NAMESPACE``, which the cache never fails open on,
and the class decides what an outage means with ONE attribute,
``fail_closed``:

- ``fail_closed = True`` — REFUSE: the cache's error goes on to
  ``core.middleware.strict_cache``, which answers 503
  ``temporarily_unavailable`` (the rule beside
  ``core.caches.STRICT_KEY_PREFIXES``), logged at ERROR once per burst.
  Not a 429: the caller sent nothing too many, and with no history there
  is no honest ``Retry-After``. Which scopes: the endpoint is a secret
  or existence oracle (coupon, gift card, order lookup, staff login),
  moves money or stock (order create, payment), makes us send email to
  an address the caller chooses (newsletter), stores the caller's
  uploads (contact attachments), or spends a third party's budget on the
  caller's behalf (VIES, the ACS and BoxNow partner APIs).
- ``fail_closed = False`` — ALLOW and log at WARNING, once per burst:
  every other scope (browsing, search, cart edits, analytics events,
  messages to the store's own inbox).

Every throttle is built on the classes here, the ``anon``/``user`` budgets
too (``AnonIpRateThrottle``, ``UserBudgetThrottle``): DRF's own classes key
an anonymous caller on ``get_ident``, Traefik's peer, which many visitors
share (``docs/client-ip.md``). ``ResilientThrottleMixin.get_ident`` is the
proven visitor instead, and no module outside this one imports
``rest_framework.throttling`` (``tests/unit/core/test_throttle_outage.py``).
A throttle one app uses lives in that app's ``throttles`` module; the ones
here are the bases and those several apps share (view counts, staff
login).
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
from core.client_ip import client_ip_or_peer

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
    docstring for which scopes refuse.

    An anonymous caller is the proven visitor (``get_ident``), never DRF's
    rightmost ``X-Forwarded-For`` hop: that is Traefik's peer, a Cloudflare
    edge node shared by everyone behind it (an internal ``10.42.x.x`` SNAT
    address before the Traefik Service moved to ``externalTrafficPolicy:
    Local``, proven in production 2026-09-16), so one caller could spend
    everyone's budget: at ``order_create_anon`` 10/minute, lock all guests
    out of checkout. ``client_ip_or_peer`` falls back to that peer only for
    a request whose provenance cannot be proven, because it is coarse but
    cannot be forged.
    """

    fail_closed: ClassVar[bool] = False
    cache_format = STRICT_NAMESPACE + "throttle_%(scope)s_%(ident)s"

    def get_ident(self, request) -> str:
        return client_ip_or_peer(request)

    def allow_request(self, request, view) -> bool:
        try:
            return self.take(request, view)
        except REDIS_UNAVAILABLE as exc:
            scope = getattr(self, "scope", None)
            if self.fail_closed:
                _denied_while_unavailable.report(scope, exc)
                raise
            _allowed_while_unavailable.report(scope, exc)
            return True

    def take(self, request, view) -> bool:
        """The budget check itself, which the outage policy above wraps:
        DRF's. A budget that must count requests arriving together
        overrides it (``GiftCardCheckThrottle``)."""
        return super().allow_request(request, view)  # type: ignore[misc]


def _gateway_ident(request, *, order: bool = False) -> str | None:
    """The cart or order to throttle on when the agent gateway calls.

    The gateway authenticates itself with the ``X-Internal-Gateway``
    shared secret (its ``INTERNAL_EVENTS_SECRET``). Returns ``None`` —
    meaning "throttle normally", on the address — unless the secret is
    configured, matches, and the request names what it acts on and that
    exists in this store: a cart (a cart token, or ``X-Cart-Id``) or, for
    the payment calls that ask with ``order``, only the order the route
    authorizes (its ``pk`` with that order's ``?uuid=``; cart headers are
    not read, so they cannot give an order a fresh budget, and a payment
    route without an order, the gift-card purchase, keeps the address).
    A cart token and an order uuid both open what they name, so neither
    is ever kept in a key. What counts as existing, and how each id is
    read, is
    ``cart.agent_token.existing_gateway_subject``: an id that names nothing
    must not open a budget of its own, or a client making ids up would
    never meet a limit.

    Settled once per request: every throttle of the view asks.
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
    settled = getattr(request, "_gateway_idents", None)
    if settled is None:
        settled = request._gateway_idents = {}
    if order not in settled:
        # Imported here, not at the top: ``DEFAULT_THROTTLE_CLASSES`` names
        # this module, so DRF imports it while ``rest_framework.views`` is
        # still loading, and ``cart.agent_token`` reaches drf-spectacular,
        # which needs ``APIView`` (a circular import at start-up).
        from cart.agent_token import existing_gateway_subject

        settled[order] = existing_gateway_subject(request, order=order)
    return settled[order]


class GatewayIdentMixin(ResilientThrottleMixin):
    """Identify a request the agent gateway authenticates by what it names.

    Every buyer an AI platform serves reaches Django through the gateway
    from the platform's address, so the per-address identity would put
    them all in one budget. A request carrying the gateway's secret and
    a cart or an order is identified by it instead (``_gateway_ident``),
    so one cart or order still cannot exceed the rate; everyone else
    keeps the proven visitor. DRF asks ``get_ident`` only for a caller
    who is not signed in, so a signed-in agent is keyed on its account.

    Only a cart or order that exists earns the key, never a bare id
    (``_gateway_ident``). A throttle that guards the payment calls sets
    ``gateway_names_order``, which cart routes never do: it is keyed on
    the order the route authorizes alone, never on a cart, and on the
    address where the route names no order (the gift-card purchase).

    The rule lives here, once, for both halves of a pair
    (``GatewayAnonThrottle`` and the ``UserRateThrottle`` beside it),
    for ``GiftCardCodeThrottle`` and for ``CouponApplyThrottle``.
    """

    gateway_names_order: ClassVar[bool] = False

    def get_ident(self, request) -> str:
        if ident := _gateway_ident(request, order=self.gateway_names_order):
            return f"gw:{ident}"
        return super().get_ident(request)


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

    Keyed by user id when authenticated and by the proven client IP
    otherwise (``ResilientThrottleMixin.get_ident``), so one signed-in
    caller cannot spend another's budget and a shared office IP no longer
    puts every colleague in one bucket.
    """

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            ident = f"user:{user.pk}"
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class UserBudgetThrottle(UserOrIpRateThrottle):
    """The day-scale ``user`` budget (``DEFAULT_THROTTLE_CLASSES``), the
    sibling of ``AnonIpRateThrottle``'s ``anon``. Fails open: browsing
    must survive a Redis blip."""

    scope = "user"


class AnonIpRateThrottle(ResilientThrottleMixin, AnonRateThrottle):
    """The anonymous half of an Anon/User pair, keyed on the visitor
    (``ResilientThrottleMixin.get_ident``); on its own, the day-scale
    ``anon`` budget (``DEFAULT_THROTTLE_CLASSES``), failing open.

    Anon-only by design: DRF's ``get_cache_key`` is ``None`` for an
    authenticated request, whose user-keyed sibling covers it.
    """


class GatewayAnonThrottle(GatewayIdentMixin, AnonIpRateThrottle):
    """The anonymous half of an Anon/User pair, keyed on the cart or order
    when the agent gateway asks (``GatewayIdentMixin``), else on the
    visitor.

    Anonymous only, as its parent: a gateway request signed in as an
    account (a linked agent) is throttled by the user budget alone.
    """


class GatewayUserRateThrottle(GatewayIdentMixin, UserRateThrottle):
    """The signed-in half of an Anon/User pair: keyed on the account, and
    on the cart or order an anonymous agent-gateway request names
    (``GatewayIdentMixin``)."""


class UserIdRateThrottle(ResilientThrottleMixin, UserRateThrottle):
    """DRF's ``UserRateThrottle`` with this module's outage policy and
    proven-visitor identity (``ResilientThrottleMixin``)."""


class ViewCountThrottle(UserOrIpRateThrottle):
    """Tight per-caller throttle for the view-count increments — products
    and blog posts share one ``view_count`` budget per visitor."""

    scope = "view_count"


class StaffLoginThrottle(ResilientThrottleMixin, ScopedRateThrottle):
    """The platform staff token endpoint — a credential-guessing surface
    (``tenant/staff_api.py``). Scoped by the view's ``throttle_scope``,
    and keyed like every throttle here on the proven caller, so one
    caller behind a shared edge node cannot spend everyone's budget."""

    fail_closed = True
