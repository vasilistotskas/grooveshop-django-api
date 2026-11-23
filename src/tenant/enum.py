"""The tenant app's choices: a store's plan, vertical and suspension
reason, a member's role, and a staff token's scope."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class TenantPlan(models.TextChoices):
    TRIAL = "trial", _("Trial")
    BASIC = "basic", _("Basic")
    PRO = "pro", _("Pro")
    ENTERPRISE = "enterprise", _("Enterprise")


class StoreVertical(models.TextChoices):
    """What kind of catalogue a store sells.

    A platform-level fact about the tenant, set at onboarding, that
    downstream features read to pick sensible defaults — today the
    recommendation engine's per-surface strategy presets
    (``src/recommendation/presets.py``), keyed by this enum. A store that
    fits none of the specific verticals is ``general``.
    """

    GENERAL = "general", _("General")
    FASHION = "fashion", _("Fashion & apparel")
    PLANTS_GARDEN = "plants_garden", _("Plants & garden")
    ELECTRONICS = "electronics", _("Electronics & gadgets")
    FOOD = "food", _("Food & drink")


class SuspendedReason(models.TextChoices):
    """Why a tenant is suspended — abuse and non-payment are different.

    ``MANUAL`` is any operator-initiated suspension (abuse, legal,
    operator judgement); ``BILLING`` is the dunning task acting on a
    lapsed term. The distinction is load-bearing: renewal-driven
    auto-reactivation (src/tenant/signals.py) is scoped to BILLING only, so
    recording a payment can never resurrect a store an operator took
    down on purpose.
    """

    MANUAL = "manual", _("Manual")
    BILLING = "billing", _("Billing")


class TenantMembershipRole(models.TextChoices):
    """Role a user holds inside a specific tenant.

    The global ``User.is_staff`` / ``is_superuser`` flags keep their
    platform-wide meaning (platform operators who manage Tenant rows in
    the public-schema admin). These per-tenant roles govern what a user
    can do *inside* a tenant they belong to. Customers hold none: they
    are scoped to a store by living in that store's schema, not by a
    membership row (see src/tenant/membership.py).

    - STAFF   — can view the tenant's operational admin (orders,
                products) but cannot change tenant settings or invite
                other staff.
    - ADMIN   — full admin within the tenant (settings, team), except
                what the owner keeps (``tenant.role_scopes.
                OWNER_ONLY_PERMISSIONS``: the profit report, expenses and
                product cost prices).
    - OWNER   — same as ADMIN plus those, and cannot be demoted/removed by
                other admins; the tenant must always have at least one
                owner (``UserTenantMembership`` refuses to remove the last).
    """

    STAFF = "staff", _("Staff")
    ADMIN = "admin", _("Admin")
    OWNER = "owner", _("Owner")


class StaffTokenScope(models.TextChoices):
    """What a ``PlatformStaffToken`` may be used for."""

    API = "api", _("API")
    BUILDER = "builder", _("Page builder session")
