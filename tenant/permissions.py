"""Tenant-scoped feature-flag permission classes.

These permission classes gate entire endpoint groups behind plan-level
feature flags stored on the ``Tenant`` model. They are *plan-level*
controls — an operator may independently enable/disable feature behaviour
at runtime via ``extra_settings`` (e.g. ``LOYALTY_ENABLED``), but the
tenant flag is the outer gate: if the plan does not include the feature,
the endpoint must be invisible regardless of extra_settings.

Design choices
--------------
* Returns 404 (``NotFound``), not 403. A 403 leaks that the endpoint
  exists; 404 makes a disabled feature indistinguishable from a route
  that was never registered. This is important for plan-level hiding.
* Public-schema requests (``connection.tenant`` is None) are never
  gated — those are platform-operator calls that must always succeed.
* Does *not* bypass authentication: when chained with
  ``IsAuthenticated`` / ``IsAuthenticatedOrReadOnly`` / ``IsAdminUser``
  the auth check fires first (DRF evaluates permissions left-to-right),
  so a disabled-feature 404 only reaches authenticated callers. For
  anonymous endpoints (blog, tiers) the feature gate fires for
  everyone.

Extra-settings vs. Tenant flags
---------------------------------
The ``extra_settings`` flags (e.g. ``LOYALTY_ENABLED``) are operational
levers — a staff user can flip them at runtime inside the plan they have.
The Tenant model flags (``loyalty_enabled``, ``blog_enabled``) are
plan-level gates — they indicate whether the plan the tenant subscribed
to includes the feature at all. Both must be True for a feature to be
fully accessible.
"""

from __future__ import annotations

from django.db import connection
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission


class IsTenantFeatureEnabled(BasePermission):
    """Base permission that gates a feature behind a Tenant flag.

    Subclasses MUST set ``feature_flag`` to the name of a BooleanField
    on ``Tenant`` (e.g. ``"loyalty_enabled"``). The class should not be
    used directly on viewsets — use the concrete subclasses below.

    Raises ``NotFound`` (HTTP 404) when the feature is disabled so that
    the endpoint is indistinguishable from a non-existent route. This
    hides plan tier information from potential enumerators.
    """

    feature_flag: str = ""

    def has_permission(self, request, view) -> bool:
        tenant = getattr(connection, "tenant", None)
        if tenant is None:
            # Public schema — platform operator, never gated.
            return True
        enabled = bool(getattr(tenant, self.feature_flag, True))
        if not enabled:
            raise NotFound()
        return True


class IsLoyaltyEnabled(IsTenantFeatureEnabled):
    """Deny access with 404 when the tenant's loyalty plan flag is off."""

    feature_flag = "loyalty_enabled"


class IsBlogEnabled(IsTenantFeatureEnabled):
    """Deny access with 404 when the tenant's blog plan flag is off."""

    feature_flag = "blog_enabled"
