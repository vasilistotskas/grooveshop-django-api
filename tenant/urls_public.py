"""URL conf that always runs in public schema context.

Mounted via PUBLIC_SCHEMA_URLCONF in settings.py — i.e. served only on
the platform control-plane host (``platform.grooveshop.space``).

Tenant admin + platform-staff endpoints run here (public schema only).
Everything else is ``core.urls.public_shared_urlpatterns`` — the SHARED
subset of the main URL surface: admin/auth/editor infra, OAuth AS
discovery, health, schema, and shared reference/control-plane data
(country/region, ``tenant-resolve``, ``tenant-memberships-mine``). It
deliberately OMITS the merchant storefront/commerce API (product, order,
cart, user, blog, loyalty, search, pay_way, shipping, notification,
contact, tag, page_config, agent) — those live in ``core.urls`` and are
served on tenant hosts only, so the control plane never exposes a store's
data even behind the platform auth wall (defence in depth).

NOTE: We import the shared list from ``core.urls`` and extend it instead
of using ``include("core.urls")`` because that list contains
``i18n_patterns()`` entries, and Django forbids ``i18n_patterns`` inside
an ``include()``.
"""

from django.urls import path

from admin.platform_site import platform_admin_site
from core.urls import public_shared_urlpatterns
from tenant.staff_api import (
    PlatformStaffLoginView,
    PlatformStaffLogoutView,
)
from tenant.views import TenantAdminViewSet

# Manual path() patterns — consistent with the rest of the codebase which
# uses explicit urlpatterns instead of DefaultRouter auto-registration.
_admin_list = TenantAdminViewSet.as_view({"get": "list", "post": "create"})
_admin_detail = TenantAdminViewSet.as_view(
    {
        "get": "retrieve",
        "put": "update",
        "patch": "partial_update",
        "delete": "destroy",
    }
)

urlpatterns = [
    # The control-plane admin. Listed BEFORE ``public_shared_urlpatterns`` so it
    # shadows the shared tenant admin that ``core.urls`` mounts at the
    # same path: Django resolves in order, and on the public schema the
    # platform site is the one that should answer. Tenant hosts never
    # reach this module — it is only loaded via PUBLIC_SCHEMA_URLCONF.
    path("admin/", platform_admin_site.urls),
    # Staff API tokens are minted on the PLATFORM host only — the
    # storefront URLconf never mounts these, which is half of the wall
    # that keeps customer logins from ever producing a staff token
    # (the other half: PlatformStaffBackend is inert in global
    # authenticate()). See docs/api-staff-identity.md.
    path(
        "api/v1/platform/auth/login",
        PlatformStaffLoginView.as_view(),
        name="platform-staff-login",
    ),
    path(
        "api/v1/platform/auth/logout",
        PlatformStaffLogoutView.as_view(),
        name="platform-staff-logout",
    ),
    path("api/v1/tenant/admin/", _admin_list, name="tenant-admin-list"),
    path(
        "api/v1/tenant/admin/<int:pk>/",
        _admin_detail,
        name="tenant-admin-detail",
    ),
] + public_shared_urlpatterns
