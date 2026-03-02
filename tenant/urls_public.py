"""URL conf that always runs in public schema context.

Mounted via PUBLIC_SCHEMA_URLCONF in settings.py.
Tenant resolve and admin endpoints run here (public schema only).
All other URLs fall through to the main ROOT_URLCONF.

NOTE: We import ``urlpatterns`` from ``core.urls`` and extend the list
instead of using ``include("core.urls")`` because ``core.urls`` uses
``i18n_patterns()`` at module level, and Django forbids
``i18n_patterns`` inside an ``include()``.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.urls import urlpatterns as core_urlpatterns
from tenant.views import TenantAdminViewSet, tenant_resolve

router = DefaultRouter()
router.register(
    r"api/v1/tenant/admin",
    TenantAdminViewSet,
    basename="tenant-admin",
)

urlpatterns = [
    path("api/v1/tenant/resolve", tenant_resolve, name="tenant-resolve"),
    path("", include(router.urls)),
] + core_urlpatterns
