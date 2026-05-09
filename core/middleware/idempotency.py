"""Idempotency-Key middleware.

When a client sends ``Idempotency-Key: <key>`` on a mutating request
(POST/PUT/PATCH/DELETE), the response is cached for 24 hours scoped to
the authenticated user (or session) plus the request method and path.
Retries of the same request — e.g. after a network drop during a Stripe
PaymentIntent creation — return the cached response instead of
re-executing the handler, eliminating duplicate orders / charges /
side effects.

Design choices:

* Only applies when the client explicitly sends the header (RFC draft
  behavior). Silent, server-wide idempotency would surprise clients.
* Only caches 2xx and 4xx responses. 5xx stays retryable, matching
  Stripe's own semantics.
* Scoped by ``user_id`` when authenticated and by session-key fallback
  for anonymous requests, so one user's key cannot collide with another.
* Method + path are mixed into the cache key so the same
  ``Idempotency-Key`` on two different endpoints does not alias.
* Keys are hashed (SHA-256, 32 hex chars) before Redis storage to bound
  key length and avoid logging sensitive client-chosen values.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from django.core.cache import caches
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

IDEMPOTENCY_HEADER = "HTTP_IDEMPOTENCY_KEY"
IDEMPOTENCY_TTL_SECONDS = 60 * 60 * 24  # 24h
IDEMPOTENT_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CACHE_ALIAS = "default"
KEY_NAMESPACE = "idem"
MAX_CACHED_BODY_BYTES = 256 * 1024  # 256 KB — responses larger than this
# (e.g. streaming file downloads) are not worth caching for idempotency.


def _get_real_ip(request: HttpRequest) -> str:
    """Return the real client IP, respecting X-Real-IP set by Traefik.

    Preference order:
    1. ``X-Real-IP`` header injected by our trusted reverse proxy (Traefik).
    2. Rightmost entry in ``X-Forwarded-For`` (the one the proxy appended).
    3. ``REMOTE_ADDR`` as final fallback for direct / test connections.
    """
    real_ip = request.META.get("HTTP_X_REAL_IP", "").strip()
    if real_ip:
        return real_ip
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    entries = [e.strip() for e in xff.split(",") if e.strip()]
    if entries:
        return entries[-1]  # rightmost (trusted proxy)
    return request.META.get("REMOTE_ADDR", "unknown")


def _scope_id(request: HttpRequest) -> str:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return f"u:{user.pk}"
    session_key = getattr(
        getattr(request, "session", None), "session_key", None
    )
    if session_key:
        return f"s:{session_key}"
    # Last-resort fallback — keyed to real client IP. Weaker guarantee but
    # still prevents accidental cross-client aliasing on retries.
    return f"ip:{_get_real_ip(request)}"


def _cache_key(request: HttpRequest, idempotency_key: str) -> str:
    raw = f"{_scope_id(request)}|{request.method}|{request.path}|{idempotency_key}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"{KEY_NAMESPACE}:{digest}"


class IdempotencyMiddleware(MiddlewareMixin):
    def process_request(self, request: HttpRequest) -> HttpResponse | None:
        if request.method not in IDEMPOTENT_METHODS:
            return None

        idempotency_key = request.META.get(IDEMPOTENCY_HEADER)
        if not idempotency_key:
            return None

        key = _cache_key(request, idempotency_key)
        cached = caches[CACHE_ALIAS].get(key)
        if cached is None:
            # First seen — stash the key on the request so process_response
            # knows to cache the outcome.
            request._idempotency_cache_key = key  # type: ignore[attr-defined]
            return None

        logger.info(
            "idempotency replay",
            extra={
                "method": request.method,
                "path": request.path,
                "scope": _scope_id(request),
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

        status = response.status_code
        # Skip 5xx (retryable by design) and redirects (handler-specific).
        if status >= 500 or 300 <= status < 400:
            return response

        content = getattr(response, "content", b"") or b""
        if len(content) > MAX_CACHED_BODY_BYTES:
            return response

        body: Any
        content_type = response.get("Content-Type", "")
        if content and "application/json" in content_type:
            try:
                body = json.loads(content.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return response
        else:
            # Non-JSON responses are stashed as a base64-free ASCII shell —
            # the replay above only rehydrates JSON bodies, so anything
            # else is skipped cleanly.
            return response

        headers_to_preserve = {
            name: value
            for name, value in response.items()
            if name.lower()
            in {"content-type", "x-correlation-id", "location", "vary"}
        }

        caches[CACHE_ALIAS].set(
            key,
            {
                "status": status,
                "body": body,
                "headers": headers_to_preserve,
            },
            timeout=IDEMPOTENCY_TTL_SECONDS,
        )
        return response
