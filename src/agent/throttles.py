"""The agent app's throttles, built on ``core.api.throttling`` (its
module docstring says what each one does while Redis is unreachable)."""

from __future__ import annotations

from core.api.throttling import (
    UserOrIpRateThrottle,
)


class StaffAgentThrottle(UserOrIpRateThrottle):
    """The store-admin agent surface (``src/agent/staff_views.py``).

    Keyed by the staff member, and per store, since the cache key carries
    the schema (``tenant.cache.make_tenant_key``): one AI app's burst of
    tool calls spends that person's budget at that store, nobody else's.
    """

    scope = "agent_staff"
