"""Idempotency-Key middleware.

When a client sends ``Idempotency-Key: <key>`` on a mutating request
(POST/PUT/PATCH/DELETE), the response is cached for 24 hours for the
caller that sent it, plus the request method and path. Retries of the
same request — e.g. after a network drop during a Stripe PaymentIntent
creation, or a chat turn the shopper sends again — return the cached
response instead of re-executing the handler, eliminating duplicate
orders / charges / cart adds.

Design choices:

* Only applies when the client explicitly sends the header (RFC draft
  behavior). Silent, server-wide idempotency would surprise clients.
* Only caches 2xx and 4xx responses. 5xx stays retryable, matching
  Stripe's own semantics.
* A key belongs to the shopper, not to the connection. Its scope is the
  authenticated principal, authenticated the way the view will do it:
  DRF resolves a Bearer token inside the view, after every
  ``process_request``, so the decision is made in ``process_view`` with
  the view's own authenticators. An anonymous request is scoped to the
  cart it names (``X-Cart-Token``, else ``X-Cart-Id``), else its
  session, and to the client address only as the last resort. An
  address is a connection, not a caller: two gateway pods, two proxies
  or a phone changing networks give one shopper two of them, and a
  retry under the other one was not recognised.
* A request whose credentials the view's authenticators refuse is not
  guarded: the view refuses it too, and there is no caller to scope it
  to.
* The cache is the store's (``tenant.cache.make_tenant_key``), so a
  scope never reaches another tenant.
* Method + path are mixed into the cache key so the same
  ``Idempotency-Key`` on two different endpoints does not alias.
* Keys are hashed (SHA-256, 32 hex chars) before Redis storage to bound
  key length and avoid logging sensitive client-chosen values.
* Bounded per caller. The client picks the key, so without a cap this is
  a 24-hour Redis write primitive anyone can drive before any DRF
  throttle sees it. The budget belongs to the principal, or for an
  anonymous caller to the client address, never to a cart or a session,
  which anyone can mint. Over budget, the request runs normally and is
  simply not cached.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from django.core.cache import caches
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.deprecation import MiddlewareMixin
from rest_framework.exceptions import APIException
from rest_framework.request import Request
from rest_framework.views import APIView

from cart.agent_token import cart_token_from_request, hash_cart_token
from cart.services import cart_uuid_from_request
from core.client_ip import client_ip_or_peer

logger = logging.getLogger(__name__)

IDEMPOTENCY_HEADER = "HTTP_IDEMPOTENCY_KEY"
IDEMPOTENCY_TTL_SECONDS = 60 * 60 * 24  # 24h
IDEMPOTENT_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CACHE_ALIAS = "default"
KEY_NAMESPACE = "idem"
# Sentinel + TTL for the in-flight reservation (G0103). The TTL is a
# self-heal bound: if a worker dies mid-request without process_response
# releasing the marker, a retry is unblocked after this window.
_IN_FLIGHT_MARKER = "__idempotency_in_flight__"
_IN_FLIGHT_TTL_SECONDS = 60
MAX_CACHED_BODY_BYTES = 256 * 1024  # 256 KB — responses larger than this
# (e.g. streaming file downloads) are not worth caching for idempotency.

# The client chooses the key, so without a bound this middleware is a
# 24-hour Redis write primitive that anyone can drive: a fresh
# Idempotency-Key on every request mints a new entry holding up to
# MAX_CACHED_BODY_BYTES, and it runs at the middleware layer — before
# DRF ever reaches a throttle. The deployed Redis is 614 MB on
# allkeys-lru, so filling it evicts the sessions, carts, WebSocket
# tickets and throttle counters that share it.
#
# Real usage is small: one key per checkout attempt, reused across its
# retries (the storefront's useCheckoutSubmit.ts), and one per chat add
# from the agent gateway, which a retried turn repeats. A hundredfold
# headroom over that is still a bound.
MAX_KEYS_PER_CALLER = 200
# Stripe's own limit, and long enough for any UUID scheme.
MAX_KEY_LENGTH = 255


def _get_real_ip(request: HttpRequest) -> str:
    """Return the client address, the last-resort scope and budget.

    ``core.client_ip.client_ip_or_peer``: the edge-proven visitor, else the
    peer Traefik saw, else ``REMOTE_ADDR``. Never a bare ``X-Real-IP``: on
    the SSR path Nuxt fills it from the caller's own ``CF-Connecting-IP``,
    so a caller at a node IP could pick a fresh budget per request.
    """
    return client_ip_or_peer(request) or "unknown"


class _CredentialsRefused(Exception):
    """The view's authenticators refused the request's credentials."""


def _principal(request: HttpRequest, view_func: Any) -> str | None:
    """The signed-in caller, authenticated the way the view will.

    Django's ``AuthenticationMiddleware`` sees a session user only. A
    Bearer token (knox, a staff or an agent token) is resolved by DRF
    inside the view, so a DRF view's own authenticators run here, and
    the view runs them again. A view that takes no credentials
    (``authentication_classes = []``) has no principal, whatever the
    request carries.
    """
    view_class = getattr(view_func, "cls", None)
    if view_class is None or not issubclass(view_class, APIView):
        user = getattr(request, "user", None)
    else:
        view = view_class(**getattr(view_func, "initkwargs", {}))
        drf_request = Request(request, authenticators=view.get_authenticators())
        try:
            user = drf_request.user
        except APIException as exc:
            raise _CredentialsRefused from exc
    if user is None or not user.is_authenticated:
        return None
    return f"u:{user.pk}"


def _scope_id(request: HttpRequest, principal: str | None) -> str:
    """Whose key this is: the principal, else the cart the anonymous
    request names, else its session, else its address."""
    if principal:
        return principal
    cart_token = cart_token_from_request(request)
    if cart_token:
        return f"ct:{hash_cart_token(cart_token)}"
    cart_uuid = cart_uuid_from_request(request)
    if cart_uuid:
        return f"c:{cart_uuid}"
    session_key = getattr(
        getattr(request, "session", None), "session_key", None
    )
    if session_key:
        return f"s:{session_key}"
    return f"ip:{_get_real_ip(request)}"


def _cache_key(request: HttpRequest, scope: str, idempotency_key: str) -> str:
    raw = f"{scope}|{request.method}|{request.path}|{idempotency_key}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"{KEY_NAMESPACE}:{digest}"


def _budget_owner(request: HttpRequest, principal: str | None) -> str:
    """Who pays for a new key: the principal, else the client address.

    Never the scope's cart or session: anyone can mint a fresh one per
    request, and with it a fresh budget.
    """
    return principal or f"ip:{_get_real_ip(request)}"


def _budget_key(owner: str) -> str:
    """Counter of distinct keys this caller has minted in the TTL window."""
    digest = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:32]
    return f"{KEY_NAMESPACE}:budget:{digest}"


def _claim_budget(request: HttpRequest, owner: str) -> bool:
    """Charge one entry to this caller, or refuse when it is spent.

    ``add`` then ``incr``: ``add`` initialises the counter and sets the
    window exactly once, and Redis' ``INCR`` is atomic, so concurrent
    requests cannot both see a stale count. The window is not sliding —
    it starts at the caller's first key and lasts as long as the entries
    it is counting, which is the property that matters.

    A backend failure returns True. This is a flood bound, not a
    security gate, and refusing to reserve because Redis hiccuped would
    turn a cache blip into duplicate orders.
    """
    cache = caches[CACHE_ALIAS]
    budget_key = _budget_key(owner)
    try:
        cache.add(budget_key, 0, IDEMPOTENCY_TTL_SECONDS)
        used = cache.incr(budget_key)
    except Exception:
        logger.warning("idempotency budget unavailable", exc_info=True)
        return True

    if used > MAX_KEYS_PER_CALLER:
        logger.warning(
            "idempotency budget exhausted",
            extra={
                "caller": owner,
                "used": used,
                "limit": MAX_KEYS_PER_CALLER,
                "path": request.path,
            },
        )
        return False
    return True


def _in_flight() -> JsonResponse:
    """A repeat of a request that is still running. Its code tells a
    client to keep the key and ask again, unlike a refusal, which is
    cached under the key and final (docs/error-contract.md)."""
    return JsonResponse(
        {
            "detail": "A duplicate request is already in progress.",
            "code": "idempotency_in_flight",
        },
        status=409,
    )


class IdempotencyMiddleware(MiddlewareMixin):
    def process_view(
        self,
        request: HttpRequest,
        view_func: Any,
        view_args: Any,
        view_kwargs: Any,
    ) -> HttpResponse | None:
        if request.method not in IDEMPOTENT_METHODS:
            return None

        idempotency_key = request.META.get(IDEMPOTENCY_HEADER)
        if not idempotency_key:
            return None

        if len(idempotency_key) > MAX_KEY_LENGTH:
            return JsonResponse(
                {
                    "detail": (
                        "Idempotency-Key must be at most "
                        f"{MAX_KEY_LENGTH} characters."
                    ),
                    "code": "idempotency_key_too_long",
                },
                status=400,
            )

        try:
            principal = _principal(request, view_func)
        except _CredentialsRefused:
            return None
        scope = _scope_id(request, principal)
        key = _cache_key(request, scope, idempotency_key)
        cached = caches[CACHE_ALIAS].get(key)

        if cached == _IN_FLIGHT_MARKER:
            # A duplicate with the same key is still executing — tell the
            # client to back off rather than run the handler a second time
            # (G0103).
            return _in_flight()

        if cached is None:
            # First seen — atomically reserve the key so a concurrent
            # duplicate collides here (add() fails) instead of both
            # executing (G0103). process_response replaces the marker with
            # the real outcome, or releases it if the response isn't
            # cacheable.
            if not _claim_budget(request, _budget_owner(request, principal)):
                # Over budget: run the request normally, without a
                # reservation and without caching the outcome. Skipping
                # rather than refusing is deliberate — idempotency is a
                # protection, not a gate, and answering 429 here would
                # turn the flood bound into a denial of service against
                # whoever tripped it.
                return None

            reserved = caches[CACHE_ALIAS].add(
                key, _IN_FLIGHT_MARKER, _IN_FLIGHT_TTL_SECONDS
            )
            if not reserved:
                return _in_flight()
            request._idempotency_cache_key = key  # type: ignore[attr-defined]
            return None

        logger.info(
            "idempotency replay",
            extra={
                "method": request.method,
                "path": request.path,
                "scope": scope.split(":", 1)[0],
            },
        )

        response = JsonResponse(
            cached["body"],
            status=cached["status"],
            safe=isinstance(cached["body"], dict),
        )
        for header_name, header_value in cached.get("headers", {}).items():
            response[header_name] = header_value
        response["Idempotent-Replay"] = "true"
        return response

    def process_response(
        self, request: HttpRequest, response: HttpResponse
    ) -> HttpResponse:
        key: str | None = getattr(request, "_idempotency_cache_key", None)
        if not key:
            return response

        outcome = self._cacheable_outcome(response)
        if outcome is None:
            # Not cacheable (5xx retryable / redirect / oversized / non-JSON)
            # — RELEASE the in-flight reservation so a legitimate retry isn't
            # blocked with a 409 until the marker's TTL expires (G0103).
            caches[CACHE_ALIAS].delete(key)
            return response

        caches[CACHE_ALIAS].set(key, outcome, timeout=IDEMPOTENCY_TTL_SECONDS)
        return response

    @staticmethod
    def _cacheable_outcome(response: HttpResponse) -> dict[str, Any] | None:
        """Return the cacheable replay dict, or None when the response must
        not be cached (5xx, redirect, oversized, or non-JSON body)."""
        status = response.status_code
        if status >= 500 or 300 <= status < 400:
            return None

        content = getattr(response, "content", b"") or b""
        if len(content) > MAX_CACHED_BODY_BYTES:
            return None

        content_type = response.get("Content-Type", "")
        if not (content and "application/json" in content_type):
            return None

        try:
            body = json.loads(content.decode("utf-8"))
        except ValueError, UnicodeDecodeError:
            return None

        headers_to_preserve = {
            name: value
            for name, value in response.items()
            if name.lower()
            in {"content-type", "x-correlation-id", "location", "vary"}
        }
        return {
            "status": status,
            "body": body,
            "headers": headers_to_preserve,
        }
