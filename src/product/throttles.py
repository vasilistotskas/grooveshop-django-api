"""The product app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    UserOrIpRateThrottle,
)


class ProductAlertCreateThrottle(UserOrIpRateThrottle):
    """Per-caller budget for creating product alerts. A guest's request
    sends a confirmation email to whatever address it is given."""

    scope = "product_alert_create"
    fail_closed = True
