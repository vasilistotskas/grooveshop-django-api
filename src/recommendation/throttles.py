"""The recommendation app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    UserOrIpRateThrottle,
)


class RecommendationEventThrottle(UserOrIpRateThrottle):
    """Budget for suggestion-strip impression/click events. Its own
    scope so a scripted client cannot starve the search allowance."""

    scope = "recommendation_event"
