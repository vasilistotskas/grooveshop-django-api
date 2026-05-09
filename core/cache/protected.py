from __future__ import annotations

import re

# Patterns that must NEVER be matched by a surface purge, no matter what
# a surface registers. Includes:
#   - DRF throttle counters (rate-limiting state)
#   - allauth flow / 2FA state
#   - Knox token cache
#   - Bull queue keys (media-stream)
#   - Image processing cache
#   - Circuit breaker state
#   - dj-stripe + dj-paypal customer sync
#   - Sessions
#   - Anything that, if dropped, would log users out, drop in-flight jobs,
#     or fingerprint as suspicious activity to security middleware.
PROTECTED_FRAGMENTS: tuple[str, ...] = (
    "throttle_",
    "allauth.",
    "knox_",
    "session:",
    "sessions.cached_db",  # Django session backend, locks users out if cleared
    "bull:",
    "image:",
    "circuit_breaker:",
    "dj_stripe:",
    "dj_paypal:",
    "ratelimit:",
    "rate-limit:",
    "boxnow_widget_token:",
    "boxnow:access_token:",  # OAuth token cache; clearing forces re-auth
    "health:locks",
    "celery-task-meta-",
)

_compiled = re.compile(
    "|".join(re.escape(fragment) for fragment in PROTECTED_FRAGMENTS)
)


def is_protected(key: str) -> bool:
    return bool(_compiled.search(key))


def filter_protected(keys: list[str]) -> tuple[list[str], list[str]]:
    """Return ``(safe_keys, blocked_keys)`` after applying the deny list."""

    safe: list[str] = []
    blocked: list[str] = []
    for key in keys:
        (blocked if is_protected(key) else safe).append(key)
    return safe, blocked
