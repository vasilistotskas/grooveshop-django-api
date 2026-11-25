"""The giftcard app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import suppress

from django.conf import settings
from django.utils.crypto import salted_hmac
from redis.exceptions import LockError
from rest_framework.exceptions import ValidationError
from rest_framework.fields import SkipField

from core.api.throttling import (
    GatewayIdentMixin,
    UserOrIpRateThrottle,
)
from core.cart_id import cart_uuid_from_request

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


#: The body field a checkout names its gift cards in: the quote and
#: order creation share it.
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

    The quote and order creation each say what a card settles, or that
    a code is no card, so each is a balance oracle
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
    returns, on every route that prices such codes: the quote and order
    creation. ``serializer_class`` is the
    route's request serializer, whose ``gift_card_codes`` field reads the
    codes. A request without codes gets none.
    """
    codes = _gift_card_codes(request, serializer_class)
    return [GiftCardCodeThrottle(codes)] if codes else []
