"""Throttles, and what each one does while Redis is unreachable.

Every throttle keeps its request history in Redis. The default cache
fails open (``core.caches.CustomCache``): during an outage a read is a
miss and a write does nothing, so a throttle reading through it would
see no history and allow everything. For the day-scale ``anon`` /
``user`` browsing budgets that is right — a Redis blip must not take the
storefront API down.

It is wrong for the scopes that guard something. So every throttle
defined here uses ``ResilientThrottleMixin``: its keys live under
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

Every throttle is defined here, the ``anon``/``user`` budgets too
(``AnonIpRateThrottle``, ``UserBudgetThrottle``): DRF's own classes key an
anonymous caller on ``get_ident``, Traefik's peer, which many visitors
share (``docs/client-ip.md``). ``ResilientThrottleMixin.get_ident`` is the
proven visitor instead, and no module outside this one imports
``rest_framework.throttling`` (``tests/unit/core/test_throttle_outage.py``).
"""

import hmac
import logging
from collections.abc import Iterable, Mapping
from contextlib import suppress
from typing import ClassVar

from django.conf import settings
from django.utils.crypto import salted_hmac
from redis.exceptions import LockError
from rest_framework.exceptions import ValidationError
from rest_framework.fields import SkipField
from rest_framework.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
)

from core.caches import REDIS_UNAVAILABLE, STRICT_NAMESPACE, BurstLogger
from core.cart_id import cart_uuid_from_request
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


class ContactCreateThrottle(UserOrIpRateThrottle):
    scope = "contact"


class FeedbackCreateThrottle(UserOrIpRateThrottle):
    scope = "feedback"


class NewsletterSubscribeThrottle(UserOrIpRateThrottle):
    """Per-caller budget for the anonymous newsletter form, which sends a
    confirmation email to whatever address it is given."""

    scope = "newsletter_subscribe"
    fail_closed = True


class ProductAlertCreateThrottle(UserOrIpRateThrottle):
    """Per-caller budget for creating product alerts. A guest's request
    sends a confirmation email to whatever address it is given."""

    scope = "product_alert_create"
    fail_closed = True


class EmailUnsubscribeThrottle(UserOrIpRateThrottle):
    """Per-caller budget for the one-click unsubscribe POST.

    Fails open: an unsubscribe is a legal obligation, so a Redis outage
    must not stop one. The budget is generous because mailbox providers
    send every recipient's click from a small pool of addresses.
    """

    scope = "email_unsubscribe"


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


class PaymentAttemptThrottle(GatewayIdentMixin, UserRateThrottle):
    scope = "payment"
    fail_closed = True
    gateway_names_order = True


class PaymentAttemptAnonThrottle(GatewayAnonThrottle):
    """Starting a payment, keyed per order when the agent gateway asks for
    the link it needs right after placing an online order (the order the
    route authorizes, never a cart: ``GatewayIdentMixin``)."""

    scope = "payment_anon"
    fail_closed = True
    gateway_names_order = True


class OrderCreateThrottle(GatewayIdentMixin, UserRateThrottle):
    scope = "order_create"
    fail_closed = True


class OrderCreateAnonThrottle(GatewayAnonThrottle):
    """Anonymous checkout is a stock- and money-moving endpoint.

    Creating an order reserves or decrements stock, can mint a courier
    voucher and can open a provider payment session, all before anyone
    has authenticated. The global anon budget is a day-scale ceiling and
    does not bound a burst. An order the agent gateway places is
    budgeted per cart (``GatewayAnonThrottle``), like its cart writes.
    """

    scope = "order_create_anon"
    fail_closed = True


class CartMutationThrottle(GatewayIdentMixin, UserRateThrottle):
    scope = "cart_mutation"


class CartAgentTokenThrottle(ResilientThrottleMixin, UserRateThrottle):
    """Minting hands out a credential for the shopper's cart."""

    scope = "cart_agent_token"
    fail_closed = True


class StaffAgentThrottle(UserOrIpRateThrottle):
    """The store-admin agent surface (``agent/staff_views.py``).

    Keyed by the staff member, and per store, since the cache key carries
    the schema (``tenant.cache.make_tenant_key``): one AI app's burst of
    tool calls spends that person's budget at that store, nobody else's.
    """

    scope = "agent_staff"


class CartMutationAnonThrottle(GatewayAnonThrottle):
    scope = "cart_mutation_anon"


class CartQuoteThrottle(ResilientThrottleMixin, UserRateThrottle):
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


#: How long a caller's gift-card budget may stay held while one request
#: takes from it (a lease, should the holder die), and how long another
#: request of the same caller waits for it before it is refused.
_BUDGET_LEASE_SECONDS = 5
_BUDGET_WAIT_SECONDS = 2


class GiftCardCheckThrottle(UserOrIpRateThrottle):
    """Tight per-caller throttle for the gift-card balance check — the code
    IS the bearer secret, so this endpoint must not be enumerable.

    The check and the count are ONE step per caller. DRF's throttle reads
    the caller's history, compares it with the limit and writes it back:
    requests that arrive together all read the same history, all pass,
    and each writes one that leaves the others out, so N parallel
    guesses were N answers whatever the limit. Here the three happen
    while the budget's key is held (``CustomCache.lock``, the lease the
    carrier mutexes use), so a caller's parallel requests are counted
    one after the other. A request that cannot get the budget within
    ``_BUDGET_WAIT_SECONDS`` is refused, never let through uncounted.

    ``cost`` and ``taken`` are what a subclass changes
    (``GiftCardCodeThrottle``); both run while the budget is held.
    """

    scope = "gift_card_check"
    fail_closed = True

    def cost(self) -> int:
        """What this request takes from the budget."""
        return 1

    def taken(self) -> None:
        """Called once the request's cost has been counted."""

    def take(self, request, view) -> bool:
        if self.rate is None:
            return True
        key = self.get_cache_key(request, view)
        if key is None:
            return True
        lease = self.cache.lock(f"{key}:lease", timeout=_BUDGET_LEASE_SECONDS)
        if not lease.acquire(
            blocking=True, blocking_timeout=_BUDGET_WAIT_SECONDS
        ):
            # What ``wait()`` reads for the Retry-After of a refusal.
            self.key, self.history, self.now = key, [], self.timer()
            return self.throttle_failure()
        try:
            return super().take(request, view)
        finally:
            # A lease that outlived its timeout is no longer ours to
            # release; whoever holds it now keeps it.
            with suppress(LockError):
                lease.release()

    def throttle_success(self) -> bool:
        """Count the request's cost, all of it or none."""
        cost = self.cost()
        if cost == 0:
            return True
        if len(self.history) + cost > self.num_requests:
            return self.throttle_failure()
        self.history[:0] = [self.now] * cost
        self.cache.set(self.key, self.history, self.duration)
        self.taken()
        return True


#: The body field a checkout names its gift cards in: the quote, the
#: payment intent and order creation share it.
GIFT_CARD_CODES_FIELD = "gift_card_codes"


def _gift_card_codes(request, serializer_class) -> set[str]:
    """The gift-card codes a checkout request will be priced with.

    Taken from the request serializer's own field, by the calls the
    serializer itself makes (``get_value``, then ``run_validation``), so
    the budget and the pricing read one body one way: the list of a
    JSON body, every value of a repeated key in a form or multipart
    body, indexed form keys. A hand-written reader counted none of a
    form body's codes while the serializer priced them all.

    A field the serializer would refuse (not a list, too many codes, a
    code too long) answers 400 before anything is priced, so it carries
    no code to count. The codes are upper-cased as the gift-card service
    reads them (``GiftCardService._load_cards``; the field has trimmed
    them), so one card typed two ways is one code.
    """
    data = request.data
    if not isinstance(data, Mapping):
        return set()
    field = serializer_class(data=data).fields[GIFT_CARD_CODES_FIELD]
    try:
        codes = field.run_validation(field.get_value(data))
    except SkipField, ValidationError:
        return set()
    return {code.upper() for code in codes or () if code}


def _checkout_cart_ident(request) -> str | None:
    """The cart a checkout request prices, for the record of the codes it
    has tried: the cart token's hash, else the signed-in customer (who
    has one cart), else the guest cart's id. ``None`` when it names none.
    """
    from cart.agent_token import cart_token_from_request, hash_cart_token

    token = cart_token_from_request(request)
    if token:
        return "token:" + hash_cart_token(token)
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return f"user:{user.pk}"
    cart_uuid = cart_uuid_from_request(request)
    return f"cart:{cart_uuid}" if cart_uuid else None


class GiftCardCodeThrottle(GatewayIdentMixin, GiftCardCheckThrottle):
    """Bound GUESSING a gift-card code through checkout, not using one.

    The quote, the payment intent and order creation each say what a
    card settles, or that a code is no card, so each is a balance oracle
    like ``/giftcard/check``. But a checkout re-prices the same card on
    every change, and that is use, not guessing. So:

    * **Only a code new to the cart counts.** The cart keeps, for
      ``GIFT_CARD_CODES_SEEN_TTL_SECONDS``, the codes it has already
      been allowed to try (as keyed digests, never the code). Sending
      one of them again costs nothing; each new one costs one, and a
      request with several new codes costs that many.
    * **Who pays.** The signed-in customer or the proven address, as
      ``/giftcard/check``, and from the same ``gift_card_check`` budget:
      a caller's guesses are counted once, wherever they are made.
      A request the agent gateway makes for a guest is counted on its
      cart instead (``_gateway_ident``, as ``GatewayAnonThrottle``):
      every buyer of a platform arrives from the platform's address.

    The count is taken while the budget is held (see the parent), so
    requests that arrive together are counted one after the other.
    A request this budget refuses records nothing, so its codes are
    still new the next time. (One that another throttle of the view
    refuses may already have been counted and recorded here, DRF asks
    every throttle: each code still counts once.) A changed cart id loses
    the record, never the budget. During a cache outage the record
    reads empty (every code counts) and the budget itself refuses, as
    its parent does.
    """

    seen_format = "gift_card_codes_seen_%(cart)s"

    def __init__(self, codes: Iterable[str] = ()):
        super().__init__()
        self.codes = set(codes)

    @staticmethod
    def _digest(code: str) -> str:
        return salted_hmac(
            "giftcard.code-seen", code, algorithm="sha256"
        ).hexdigest()

    def allow_request(self, request, view) -> bool:
        cart = _checkout_cart_ident(request)
        self.seen_key = self.seen_format % {"cart": cart} if cart else None
        self.digests = {self._digest(code) for code in self.codes}
        # Nothing new is use, not a guess: free, and the budget is not
        # even held for it.
        if not self._new_to_cart():
            return True
        return super().allow_request(request, view)

    def _new_to_cart(self) -> set[str]:
        self.seen = (
            set(self.cache.get(self.seen_key, ())) if self.seen_key else set()
        )
        return self.digests - self.seen

    def cost(self) -> int:
        """Every code new to the cart, read again now that the budget is
        held: a request that arrived together with this one may have
        just been counted for the same code."""
        self.new = self._new_to_cart()
        return len(self.new)

    def taken(self) -> None:
        """Remember the codes for the cart."""
        if self.seen_key:
            self.cache.set(
                self.seen_key,
                sorted(self.seen | self.new),
                settings.GIFT_CARD_CODES_SEEN_TTL_SECONDS,
            )


def gift_card_code_throttles(
    request, serializer_class
) -> list[GiftCardCodeThrottle]:
    """The extra budget of a checkout request that carries gift-card codes
    (``GiftCardCodeThrottle``). A view's ``get_throttles`` adds what this
    returns, on every route that prices such codes: the quote, the
    payment intent and order creation. ``serializer_class`` is the
    route's request serializer, whose ``gift_card_codes`` field reads the
    codes. A request without codes gets none.
    """
    codes = _gift_card_codes(request, serializer_class)
    return [GiftCardCodeThrottle(codes)] if codes else []


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
    """Tight per-caller throttle for the view-count increments — products
    and blog posts share one ``view_count`` budget per visitor."""

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


class OrderSpecimenThrottle(UserOrIpRateThrottle):
    """Per-caller throttle for the demo store's specimen receipt, which
    renders a PDF with WeasyPrint on the request worker for every call:
    CPU-heavy, and the shared demo logins make the caller an easy
    amplifier."""

    scope = "order_specimen"
    fail_closed = True


class RecommendationEventThrottle(UserOrIpRateThrottle):
    """Budget for suggestion-strip impression/click events. Its own
    scope so a scripted client cannot starve the search allowance."""

    scope = "recommendation_event"


class StaffLoginThrottle(ResilientThrottleMixin, ScopedRateThrottle):
    """The platform staff token endpoint — a credential-guessing surface
    (``tenant/staff_api.py``). Scoped by the view's ``throttle_scope``,
    and keyed like every throttle here on the proven caller, so one
    caller behind a shared edge node cannot spend everyone's budget."""

    fail_closed = True
