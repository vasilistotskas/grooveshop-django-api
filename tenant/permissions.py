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
        # get_current_tenant() — not connection.tenant — because the
        # latter is the PUBLIC Tenant row on the platform host once
        # bootstrap_platform has provisioned one, not None. The early
        # return below then never fired, and that row carries
        # loyalty_enabled=False by default, so every loyalty endpoint
        # 404'd on the control-plane host. The helper returns None for
        # the public schema, which is what "no tenant to gate on" means.
        from tenant.membership import get_current_tenant  # noqa: PLC0415

        tenant = get_current_tenant()
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


class IsPromotionsEnabled(IsTenantFeatureEnabled):
    """Deny access with 404 when the tenant's promotions plan flag is off."""

    feature_flag = "promotions_enabled"


class IsGiftCardsEnabled(IsTenantFeatureEnabled):
    """Deny access with 404 when the tenant's gift-cards plan flag is off."""

    feature_flag = "gift_cards_enabled"


class IsAgentCommerceEnabled(IsTenantFeatureEnabled):
    """Deny access with 404 when the tenant's agent-commerce plan flag
    is off. The gateway enforces the folded TenantConfig value for its
    own routes; this is the belt-and-braces server-side gate for the
    ``/api/v1/agent/*`` resources the gateway calls."""

    feature_flag = "agent_commerce_enabled"


class IsSettingEnabled(BasePermission):
    """Base permission that gates a feature behind an extra-setting.

    The merchant-tier twin of ``IsTenantFeatureEnabled``: subclasses
    set ``setting_key`` to a boolean extra-setting the STORE OWNER
    edits at runtime (no plan/billing dimension). Same 404 semantics —
    a disabled feature is indistinguishable from a missing route.

    ``Setting.get`` reads the ACTIVE schema, so the public/control
    plane resolves the public schema's defaults (True) and is never
    gated — mirroring the tenant-flag base's public-schema bypass.
    """

    setting_key: str = ""
    default: bool = True

    def has_permission(self, request, view) -> bool:
        from extra_settings.models import Setting  # noqa: PLC0415

        if not bool(Setting.get(self.setting_key, default=self.default)):
            raise NotFound()
        return True


class IsProductReviewsEnabled(IsSettingEnabled):
    """404 when the merchant has turned product reviews off."""

    setting_key = "PRODUCT_REVIEWS_ENABLED"


class IsBlogCommentsEnabled(IsSettingEnabled):
    """404 when the merchant has turned blog comments off."""

    setting_key = "BLOG_COMMENTS_ENABLED"


class IsFavouritesEnabled(IsSettingEnabled):
    """404 when the merchant has turned favourites/wishlist off."""

    setting_key = "FAVOURITES_ENABLED"


class IsNewsletterEnabled(IsSettingEnabled):
    """404 when the merchant has turned newsletter subscriptions off."""

    setting_key = "NEWSLETTER_ENABLED"


class IsFeedbackEnabled(IsSettingEnabled):
    """404 when the merchant has turned the feedback form off."""

    setting_key = "FEEDBACK_ENABLED"


class IsProductAlertsEnabled(IsSettingEnabled):
    """404 when the merchant has turned product alerts off."""

    setting_key = "PRODUCT_ALERTS_ENABLED"


class IsAgentCommerceRuntimeEnabled(IsSettingEnabled):
    """404 when the merchant has turned agent commerce off at runtime."""

    setting_key = "AGENT_COMMERCE_ENABLED"
