"""The b2b app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    UserOrIpRateThrottle,
)


class B2BProfileSubmitThrottle(UserOrIpRateThrottle):
    """Tight per-caller throttle for business-profile submits — each one can
    trigger an outbound VIES HTTP check (5s timeout), so an unthrottled
    endpoint is a request amplifier against both our workers and VIES."""

    scope = "b2b_profile_submit"
    fail_closed = True
