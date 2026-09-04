"""The PLATFORM control-plane admin.

``platform.grooveshop.space`` manages the platform itself: tenants,
their domains and memberships, platform staff, shared reference data and
the scheduler. It is a different job from a merchant's admin, so it is a
different site rather than the same one with sections hidden.

Why a separate ``AdminSite`` and not more conditionals on the tenant
admin:

- ``UnfoldAdminSite.settings_name`` is a class attribute and
  ``unfold.settings.get_config()`` resolves ``settings.<settings_name>``
  merged over ``CONFIG_DEFAULTS``. Pointing it at ``UNFOLD_PLATFORM``
  gives this site its own branding, sidebar, colours and dashboard
  without touching the tenant admin's ``UNFOLD`` block. Verified against
  django-unfold 0.104.1.
- Per-store models are never registered here, so they are structurally
  absent rather than reachable-but-403. The 403 guard in
  ``BaseModelAdmin._withheld_on_public`` stays as defence in depth for
  the shared site.

Registration is COPIED from the default site's registry rather than
re-declared with ``@admin.register(..., site=...)``. Every ModelAdmin
here is the same class the tenant admin uses, so the two cannot drift as
fields and inlines change.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from unfold.sites import UnfoldAdminSite

from admin.forms import PlatformAdminAuthenticationForm
from admin.mixins import AdminSiteLoginNextMixin

# Apps whose models belong on the control plane. Anything outside this
# set is per-store and lives in the tenant's own admin.
#
# ``user`` is here deliberately: it is in BOTH SHARED_APPS and
# TENANT_APPS, and platform staff identities are the PUBLIC-schema rows
# (see tenant.auth_backends.PlatformStaffBackend), so the platform must
# be able to manage them.
PLATFORM_APP_LABELS: frozenset[str] = frozenset(
    {
        "tenant",
        "user",
        "auth",
        "sites",
        "usersessions",
        # ``extra_settings`` is deliberately ABSENT: every knob in
        # EXTRA_SETTINGS is store-scoped and read from the tenant's own
        # schema (merchants edit them in THEIR admin). Nothing running
        # in the public schema reads Setting — the section here only
        # materialized meaningless public rows and invited edits that
        # silently changed nothing for any store.
        "country",
        "region",
        "django_celery_beat",
        "django_celery_results",
        # ``allauth_idp_oidc`` deliberately ABSENT: allauth.idp.oidc is
        # in TENANT_APPS only, so its tables do not exist in the public
        # schema — the sections rendered on the control plane but 500'd
        # (ProgrammingError) the moment they were opened. OIDC agent
        # clients/tokens are per-store and live in the merchant admin.
        "core",
    }
)

# Store-scoped models dragged in by an otherwise-platform app label.
# ``user`` is whitelisted for PLATFORM STAFF accounts; its newsletter /
# address / data-export models describe CUSTOMERS, who exist only in
# tenant schemas — on the public schema those sections are permanently
# empty noise.
PLATFORM_EXCLUDED_MODELS: frozenset[str] = frozenset(
    {
        "user.SubscriptionTopic",
        "user.UserSubscription",
        "user.UserAddress",
        "user.UserDataExport",
    }
)


class PlatformAdminSite(AdminSiteLoginNextMixin, UnfoldAdminSite):
    """Control-plane admin, served only on the PUBLIC schema."""

    # Its own UNFOLD config block — see module docstring.
    settings_name = "UNFOLD_PLATFORM"

    site_header = "Grooveshop Platform"
    site_title = _("Platform Admin")
    index_title = _("Control plane")

    # Its own dashboard. ``AdminSite.index()`` falls back to
    # ``"admin/index.html"``, and this project overrides that globally
    # with a MERCHANT dashboard (revenue, pending orders, "New
    # Product"). Without this the control plane rendered tenant #1's
    # dashboard and none of the platform figures it collects.
    index_template = "admin/platform_index.html"

    # Same platform-staff-only login as the shared site: credentials are
    # always checked against the PUBLIC schema's user table.
    login_form = PlatformAdminAuthenticationForm

    def get_urls(self):
        """Mount the control plane's custom pages.

        Prepended, not appended: ``AdminSite.get_urls`` ends with the
        ``<app_label>/`` catch-all, which would swallow ``plan-billing/``
        and answer 404 before our pattern is ever tried.

        The view import is local so this module keeps importing before
        the app registry is ready (it is pulled in from AppConfig).
        """
        from django.urls import path

        from admin.platform_billing import PlanBillingView

        return [
            path(
                "plan-billing/",
                self.admin_view(PlanBillingView.as_view(admin_site=self)),
                name="plan_billing",
            ),
            *super().get_urls(),
        ]

    def has_permission(self, request) -> bool:
        """Platform staff only.

        This site is mounted exclusively in ``PUBLIC_SCHEMA_URLCONF``, so
        reaching it already implies the public schema. The membership
        checks the shared site performs are meaningless here — there is
        no tenant to be a member of — but the session must still have
        been authenticated by ``PlatformStaffBackend``.
        """
        if not super().has_permission(request):
            return False

        from tenant.auth_backends import (
            is_platform_staff_session,
        )

        if not is_platform_staff_session(request):
            return False

        # Superuser, not merely is_staff.
        #
        # ``is_staff`` on a public identity is what lets a STORE
        # OPERATOR into their own store's admin — that is how
        # ``MyAdminSite`` admits them before checking membership. It is
        # therefore held by every merchant, and gating the control
        # plane on it let a merchant load THIS page: the dashboard
        # renders the whole estate — every store's name, domain, plan
        # and order count — which is another merchant's commercial
        # data.
        #
        # The app list came back empty for them (no role grants
        # anything on the public schema, where there is no current
        # tenant), so nothing was editable and no model page opened.
        # The dashboard itself was the leak, and "they cannot click
        # through" is not a boundary.
        #
        # The platform is operated by superusers; anything finer needs
        # a real platform-staff concept rather than borrowing the flag
        # that means "can open some store's admin".
        return bool(getattr(request.user, "is_superuser", False))


platform_admin_site = PlatformAdminSite(name="platform_admin")


def register_platform_models() -> None:
    """Mirror the control-plane models onto the platform site.

    Runs after ``admin.autodiscover()`` so the default registry is
    populated. Idempotent: re-registering is skipped, which matters
    because ``AppConfig.ready()`` can run more than once under the
    autoreloader.
    """
    from django.contrib import admin as django_admin

    for model, model_admin in list(django_admin.site._registry.items()):
        if model._meta.app_label not in PLATFORM_APP_LABELS:
            continue
        if model._meta.label in PLATFORM_EXCLUDED_MODELS:
            continue
        if model in platform_admin_site._registry:
            continue
        platform_admin_site.register(model, model_admin.__class__)
