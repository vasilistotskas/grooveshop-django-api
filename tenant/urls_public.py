"""URL conf that always runs in public schema context.

Mounted via PUBLIC_SCHEMA_URLCONF in settings.py.
Tenant admin endpoints run here (public schema only). Everything else
falls through to the main ROOT_URLCONF via ``core_urlpatterns``, which
already includes ``tenant.urls`` — so ``tenant-resolve`` and
``tenant-memberships-mine`` are reachable both directly (via the
tenant app's URL conf) and through this public conf.

NOTE: We import ``urlpatterns`` from ``core.urls`` and extend the list
instead of using ``include("core.urls")`` because ``core.urls`` uses
``i18n_patterns()`` at module level, and Django forbids
``i18n_patterns`` inside an ``include()``.
"""

from django.urls import path

from core.urls import urlpatterns as core_urlpatterns
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
    path("api/v1/tenant/admin/", _admin_list, name="tenant-admin-list"),
    path(
        "api/v1/tenant/admin/<int:pk>/",
        _admin_detail,
        name="tenant-admin-detail",
    ),
] + core_urlpatterns
