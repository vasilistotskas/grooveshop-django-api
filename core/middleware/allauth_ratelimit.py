"""
Rate limiting middleware for django-allauth headless endpoints.

DRF throttling only applies to /api/v1/ views. The /_allauth/ headless
endpoints bypass DRF entirely, so we need a separate rate limiter.
Uses Django's Redis cache for counters (already configured in settings).
"""

import hashlib
import logging
from typing import Callable

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse

logger = logging.getLogger(__name__)

# (path_prefix, per_minute, per_hour) — None means unlimited
_ALLAUTH_RATE_LIMITS: list[tuple[str, int | None, int | None]] = [
    ("/_allauth/app/v1/auth/login", 10, 60),
    ("/_allauth/app/v1/auth/signup", 5, 20),
    ("/_allauth/app/v1/auth/password/request", 5, 10),
    ("/_allauth/app/v1/auth/code/request", 5, 10),
    ("/_allauth/app/v1/auth/code/confirm", 10, 30),
    ("/_allauth/app/v1/auth/2fa/authenticate", 10, 30),
    ("/_allauth/app/v1/auth/2fa/reauthenticate", 10, 30),
    ("/_allauth/app/v1/auth/email/verify", 10, 30),
]


def _client_key(request: HttpRequest) -> str:
    """Return a stable, non-reversible identifier for the requesting client."""
    # Prefer real IP (behind proxy), fall back to REMOTE_ADDR.
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[
        0
    ].strip() or request.META.get("REMOTE_ADDR", "unknown")
    return hashlib.sha256(ip.encode()).hexdigest()[:32]


def _is_rate_limited(cache_key: str, limit: int, window_seconds: int) -> bool:
    """
    Sliding-window counter using atomic cache increment.
    Returns True when the request should be blocked.

    Uses add() + incr() to avoid the get-then-set race condition:
    - cache.add() is atomic: only sets the key if it doesn't exist.
    - cache.incr() is atomic: increments without race conditions.
    - On first hit, add() succeeds and incr() brings the value to 1.
    - On subsequent hits, add() is a no-op and incr() atomically increments.
    """
    # Try to initialise the key with TTL atomically. If the key already
    # exists, add() is a no-op (returns False) and expiry is unchanged.
    cache.add(cache_key, 0, window_seconds)
    current = cache.incr(cache_key)
    return current > limit


class AllAuthRateLimitMiddleware:
    """
    Rate limit the allauth headless API endpoints.

    Only POST/PUT/PATCH requests are rate-limited (reads are harmless).
    Skips when DEBUG=True or DISABLE_CACHE=True (test environments).
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response
        self.disabled = getattr(settings, "DEBUG", False) or getattr(
            settings, "DISABLE_CACHE", False
        )

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not self.disabled and request.method in ("POST", "PUT", "PATCH"):
            response = self._check_rate_limit(request)
            if response is not None:
                return response
        return self.get_response(request)

    def _check_rate_limit(self, request: HttpRequest) -> HttpResponse | None:
        path = request.path_info

        for path_prefix, per_minute, per_hour in _ALLAUTH_RATE_LIMITS:
            if not path.startswith(path_prefix):
                continue

            client = _client_key(request)
            cache_prefix = f"allauth_rl:{path_prefix.replace('/', '_')}"

            if per_minute and _is_rate_limited(
                f"{cache_prefix}:min:{client}", per_minute, 60
            ):
                logger.warning(
                    "AllAuth rate limit (per-minute) hit",
                    extra={"path": path, "client_hash": client[:8]},
                )
                return self._too_many_requests()

            if per_hour and _is_rate_limited(
                f"{cache_prefix}:hr:{client}", per_hour, 3600
            ):
                logger.warning(
                    "AllAuth rate limit (per-hour) hit",
                    extra={"path": path, "client_hash": client[:8]},
                )
                return self._too_many_requests()

            break  # matched — no need to check further rules

        return None

    @staticmethod
    def _too_many_requests() -> JsonResponse:
        return JsonResponse(
            {
                "status": 429,
                "errors": [
                    {
                        "code": "too_many_requests",
                        "message": "Too many requests. Please try again later.",
                    }
                ],
            },
            status=429,
        )
