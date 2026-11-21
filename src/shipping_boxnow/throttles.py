"""The shipping_boxnow app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    UserOrIpRateThrottle,
)


class BoxNowNearestThrottle(UserOrIpRateThrottle):
    """Per-caller throttle for the public BoxNow nearest-locker proxy, which
    forwards synchronously to the BoxNow partner API (G0059)."""

    scope = "boxnow_nearest"
    fail_closed = True
