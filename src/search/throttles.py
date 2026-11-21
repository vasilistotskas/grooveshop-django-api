"""The search app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    UserOrIpRateThrottle,
)


class SearchThrottle(UserOrIpRateThrottle):
    scope = "search"


class SearchClickThrottle(UserOrIpRateThrottle):
    scope = "search_click"
