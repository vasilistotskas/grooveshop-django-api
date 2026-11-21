"""The shipping_acs app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    UserOrIpRateThrottle,
)


class AcsAddressValidationThrottle(UserOrIpRateThrottle):
    """Per-caller throttle for the public ACS address-validation proxy, which
    forwards to the rate-limited ACS partner API (G0016)."""

    scope = "acs_address"
    fail_closed = True
